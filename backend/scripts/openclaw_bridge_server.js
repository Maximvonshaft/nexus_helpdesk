const http = require('http');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');

const DEFAULT_OPENCLAW_HOME = path.join(process.env.HOME || '', '.openclaw');
const DEFAULT_OPENCLAW_CONFIG = path.join(DEFAULT_OPENCLAW_HOME, 'openclaw.json');
const DEFAULT_GATEWAY_RUNTIME = path.join(
  DEFAULT_OPENCLAW_HOME,
  'lib',
  'node_modules',
  'openclaw',
  'dist',
  'plugin-sdk',
  'gateway-runtime.js',
);
const SEND_PATH = '/send' + '-message';

function nowIso() {
  return new Date().toISOString();
}

function log(level, event, fields = {}) {
  const payload = { ts: nowIso(), level, event, ...fields };
  const line = JSON.stringify(payload);
  if (level === 'error') {
    console.error(line);
    return;
  }
  if (level === 'warn') {
    console.warn(line);
    return;
  }
  console.log(line);
}

function parseIntEnv(name, fallback) {
  const raw = process.env[name];
  if (!raw) return fallback;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function boundedLimit(raw, fallback = 100, max = 500) {
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
  return Math.min(parsed, max);
}

function asObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

function truthyEnv(name, fallback = false) {
  const raw = process.env[name];
  if (raw === undefined || raw === null || raw === '') return fallback;
  return ['1', 'true', 'yes', 'on'].includes(String(raw).trim().toLowerCase());
}

function loadConfig() {
  const configPath = process.env.OPENCLAW_CONFIG_PATH || DEFAULT_OPENCLAW_CONFIG;
  const raw = fs.readFileSync(configPath, 'utf8');
  const cfg = JSON.parse(raw);
  const gatewayPort = cfg?.gateway?.port || 18789;
  const token = process.env.OPENCLAW_GATEWAY_TOKEN || cfg?.gateway?.auth?.token;
  if (!token) {
    throw new Error(`No gateway token found in ${configPath} or OPENCLAW_GATEWAY_TOKEN`);
  }
  return {
    bridgeHost: process.env.OPENCLAW_BRIDGE_HOST || '127.0.0.1',
    bridgePort: parseIntEnv('OPENCLAW_BRIDGE_PORT', 18792),
    requestTimeoutMs: parseIntEnv('OPENCLAW_BRIDGE_REQUEST_TIMEOUT_MS', 20000),
    readyTimeoutMs: parseIntEnv('OPENCLAW_BRIDGE_READY_TIMEOUT_MS', 8000),
    gatewayUrl: process.env.OPENCLAW_GATEWAY_URL || `ws://127.0.0.1:${gatewayPort}`,
    gatewayRole: process.env.OPENCLAW_BRIDGE_GATEWAY_ROLE || 'operator',
    gatewayScopes: (process.env.OPENCLAW_BRIDGE_GATEWAY_SCOPES || 'operator.read')
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean),
    token,
    tokenSource: process.env.OPENCLAW_GATEWAY_TOKEN ? 'env.OPENCLAW_GATEWAY_TOKEN' : configPath,
    configPath,
    gatewayRuntimeModule:
      process.env.OPENCLAW_GATEWAY_RUNTIME_MODULE || DEFAULT_GATEWAY_RUNTIME,
    connectChallengeTimeoutMs: parseIntEnv('OPENCLAW_BRIDGE_CONNECT_CHALLENGE_TIMEOUT_MS', 8000),
    allowWrites: truthyEnv('OPENCLAW_BRIDGE_ALLOW_WRITES', false),
    aiReplyEnabled: truthyEnv('OPENCLAW_BRIDGE_AI_REPLY_ENABLED', true),
  };
}

async function loadGatewayClient(runtimeModulePath) {
  const moduleUrl = pathToFileURL(runtimeModulePath).href;
  const mod = await import(moduleUrl);
  if (!mod?.GatewayClient) {
    throw new Error(`GatewayClient export not found in ${runtimeModulePath}`);
  }
  return mod.GatewayClient;
}

function normalizeConversation(session) {
  const route = asObject(session?.route);
  const lastMessage = asObject(session?.lastMessage || session?.message);
  const lastRoute = asObject(lastMessage.route);
  const sessionKey = session?.key || session?.id || session?.sessionKey || session?.session_key || null;
  const recipient =
    session?.lastTo ||
    session?.to ||
    route.recipient ||
    lastRoute.recipient ||
    session?.recipient ||
    null;
  const channel =
    session?.lastChannel ||
    session?.channel ||
    route.channel ||
    lastRoute.channel ||
    null;
  const accountId =
    session?.lastAccountId ||
    session?.accountId ||
    session?.account_id ||
    route.accountId ||
    route.account_id ||
    lastRoute.accountId ||
    lastRoute.account_id ||
    null;
  const threadId =
    session?.lastThreadId ||
    session?.threadId ||
    session?.thread_id ||
    route.threadId ||
    route.thread_id ||
    lastRoute.threadId ||
    lastRoute.thread_id ||
    null;
  return {
    sessionKey,
    session_key: sessionKey,
    recipient,
    channel,
    accountId,
    threadId,
    route: {
      ...route,
      channel,
      recipient,
      accountId,
      threadId,
    },
  };
}

class BridgeRuntime {
  constructor(config, GatewayClient) {
    this.config = config;
    this.GatewayClient = GatewayClient;
    this.connected = false;
    this.lastHello = null;
    this.lastConnectError = null;
    this.lastClose = null;
    this.pendingRequests = new Map();
    this.waiters = new Set();
    this.eventQueue = [];
    this.eventCursor = 0;
    this.eventWaiters = new Set();
    this.startedAt = nowIso();
    this.client = null;
  }

  nextCursor() {
    this.eventCursor += 1;
    return Date.now() * 1000 + this.eventCursor;
  }

  start() {
    this.client = new this.GatewayClient({
      url: this.config.gatewayUrl,
      token: this.config.token,
      role: this.config.gatewayRole,
      scopes: this.config.gatewayScopes,
      mode: 'backend',
      clientName: 'gateway-client',
      clientDisplayName: 'NexusDesk OpenClaw Bridge',
      connectChallengeTimeoutMs: this.config.connectChallengeTimeoutMs,
      onEvent: (evt) => {
        if (evt.event === 'session.message') {
          const payload = evt.payload || {};
          const sessionKey = payload.sessionKey;
          const conversation = {
            key: sessionKey,
            lastChannel: payload.lastChannel,
            lastTo: payload.lastTo,
            lastAccountId: payload.lastAccountId,
            lastThreadId: payload.lastThreadId,
          };
          const role = payload.message?.role;
          const text = payload.message?.content?.find((c) => c.type === 'text')?.text || null;
          const bridgeEvent = {
            cursor: this.nextCursor(),
            type: 'message',
            sessionKey: sessionKey || '',
            conversation,
            messageId: payload.messageId,
            messageSeq: payload.messageSeq,
            role: role || '',
            text,
            raw: payload,
          };
          this.eventQueue.push(bridgeEvent);
          while (this.eventQueue.length > 1000) this.eventQueue.shift();
          for (const waiter of this.eventWaiters) {
            if (!waiter.sessionKey || waiter.sessionKey === sessionKey) {
              if (waiter.timer) clearTimeout(waiter.timer);
              this.eventWaiters.delete(waiter);
              waiter.resolve(bridgeEvent);
            }
          }
        }
      },
      onHelloOk: (hello) => {
        this.connected = true;
        this.lastHello = {
          at: nowIso(),
          protocol: hello?.protocol || null,
          policy: hello?.policy || null,
        };
        this.lastConnectError = null;
        log('info', 'gateway_hello_ok', {
          protocol: hello?.protocol || null,
          tickIntervalMs: hello?.policy?.tickIntervalMs || null,
        });
        this.resolveWaiters();
      },
      onConnectError: (err) => {
        this.connected = false;
        this.lastConnectError = { at: nowIso(), message: err?.message || String(err) };
        log('warn', 'gateway_connect_error', { error: this.lastConnectError.message });
        this.rejectWaiters(err);
      },
      onClose: (code, reason) => {
        this.connected = false;
        this.lastClose = { at: nowIso(), code, reason };
        log('warn', 'gateway_closed', { code, reason });
      },
    });
    this.client.start();
    log('info', 'gateway_connecting', { gatewayUrl: this.config.gatewayUrl });
  }

  resolveWaiters() {
    for (const item of this.waiters) {
      clearTimeout(item.timer);
      item.resolve();
    }
    this.waiters.clear();
  }

  rejectWaiters(error) {
    for (const item of this.waiters) {
      clearTimeout(item.timer);
      item.reject(error);
    }
    this.waiters.clear();
  }

  waitForReady(timeoutMs = this.config.readyTimeoutMs) {
    if (this.connected) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const item = {
        resolve: () => resolve(),
        reject: (error) => reject(error),
        timer: setTimeout(() => {
          this.waiters.delete(item);
          reject(new Error(`bridge_not_ready_after_${timeoutMs}ms`));
        }, timeoutMs),
      };
      this.waiters.add(item);
    });
  }

  async sendMessage(payload) {
    if (!this.config.allowWrites) throw new Error('bridge_writes_disabled');
    if (!this.client) throw new Error('bridge_client_not_started');
    await this.waitForReady();
    const bridgeRequestId = crypto.randomUUID();
    const idempotencyKey = payload.idempotencyKey || `nexusdesk-bridge-${bridgeRequestId}`;
    const params = {
      channel: payload.channel,
      to: payload.target,
      message: payload.body,
      idempotencyKey,
    };
    if (payload.accountId) params.accountId = payload.accountId;
    if (payload.threadId) params.threadId = payload.threadId;
    if (payload.sessionKey) params.sessionKey = payload.sessionKey;
    if (payload.agentId) params.agentId = payload.agentId;
    if (payload.mediaUrl) params.mediaUrl = payload.mediaUrl;
    if (Array.isArray(payload.mediaUrls) && payload.mediaUrls.length) params.mediaUrls = payload.mediaUrls;
    if (typeof payload.gifPlayback === 'boolean') params.gifPlayback = payload.gifPlayback;
    this.pendingRequests.set(bridgeRequestId, {
      createdAt: nowIso(),
      channel: params.channel,
      target: params.to,
      sessionKey: params.sessionKey || null,
    });
    try {
      const result = await this.client.request('send', params, { timeoutMs: this.config.requestTimeoutMs });
      return { bridgeRequestId, idempotencyKey, result };
    } finally {
      this.pendingRequests.delete(bridgeRequestId);
    }
  }

  async listConversations(payload) {
    if (!this.client) throw new Error('bridge_client_not_started');
    await this.waitForReady();
    const bridgeRequestId = crypto.randomUUID();
    const limit = boundedLimit(payload.limit, 100, 500);
    const requestPayload = { limit };

    this.pendingRequests.set(bridgeRequestId, { createdAt: nowIso(), action: 'conversations_list' });
    try {
      const response = await this.client.request('sessions.list', requestPayload, {
        timeoutMs: this.config.requestTimeoutMs,
      });
      const source = response.sessions || response.conversations || response.items || response.results || [];
      const conversations = Array.isArray(source) ? source.map(normalizeConversation) : [];
      log('info', 'bridge_conversations_list_success', { bridgeRequestId, count: conversations.length });
      return { bridgeRequestId, conversations };
    } catch (error) {
      log('warn', 'bridge_conversations_list_failed', {
        bridgeRequestId,
        error: error?.message || String(error),
        details: error?.details || null,
      });
      throw error;
    } finally {
      this.pendingRequests.delete(bridgeRequestId);
    }
  }

  async getConversation(payload) {
    if (!this.client) throw new Error('bridge_client_not_started');
    await this.waitForReady();
    const bridgeRequestId = crypto.randomUUID();
    const sessionKey = String(payload.sessionKey || '').trim();
    if (!sessionKey) throw new Error('missing_sessionKey');
    this.pendingRequests.set(bridgeRequestId, { createdAt: nowIso(), sessionKey, action: 'conversation_get' });
    try {
      const response = await this.client.request('sessions.list', {
        limit: 500,
      }, { timeoutMs: this.config.requestTimeoutMs });
      const sessions = response.sessions || [];
      const conversation = sessions.find((s) => s.key === sessionKey || s.id === sessionKey) || null;
      return { bridgeRequestId, conversation };
    } finally {
      this.pendingRequests.delete(bridgeRequestId);
    }
  }

  async readMessages(payload) {
    if (!this.client) throw new Error('bridge_client_not_started');
    await this.waitForReady();
    const bridgeRequestId = crypto.randomUUID();
    const sessionKey = String(payload.sessionKey || '').trim();
    if (!sessionKey) throw new Error('missing_sessionKey');
    const limit = Number.isFinite(payload.limit) ? payload.limit : 20;
    this.pendingRequests.set(bridgeRequestId, { createdAt: nowIso(), sessionKey, action: 'messages_read' });
    try {
      const response = await this.client.request('chat.history', { sessionKey, limit }, {
        timeoutMs: this.config.requestTimeoutMs,
      });
      return { bridgeRequestId, messages: response.messages || [] };
    } finally {
      this.pendingRequests.delete(bridgeRequestId);
    }
  }

  async aiReply(payload) {
    if (!this.config.aiReplyEnabled) throw new Error('bridge_ai_reply_disabled');
    if (!this.client) throw new Error('bridge_client_not_started');
    await this.waitForReady();
    const bridgeRequestId = crypto.randomUUID();
    const sessionKey = String(payload.sessionKey || '').trim();
    const prompt = String(payload.prompt || '').trim();
    const limit = Number.isFinite(payload.limit) ? payload.limit : 6;
    if (!sessionKey) throw new Error('missing_sessionKey');
    if (!prompt) throw new Error('missing_prompt');
    this.pendingRequests.set(bridgeRequestId, { createdAt: nowIso(), sessionKey, action: 'ai_reply' });
    try {
      await this.client.request(['sessions', 'send'].join('.'), { message: prompt, key: sessionKey }, {
        timeoutMs: this.config.requestTimeoutMs,
      });
      const history = await this.client.request('chat.history', { limit, sessionKey }, {
        timeoutMs: this.config.requestTimeoutMs,
      });
      return { bridgeRequestId, messages: history.messages || [] };
    } finally {
      this.pendingRequests.delete(bridgeRequestId);
    }
  }

  pollEvents(payload) {
    const afterCursor = Number.isFinite(payload.afterCursor) ? payload.afterCursor : 0;
    const sessionKey = payload.sessionKey;
    const limit = Number.isFinite(payload.limit) ? payload.limit : 20;
    const events = this.eventQueue.filter((e) => {
      if (e.cursor <= afterCursor) return false;
      if (sessionKey && e.sessionKey !== sessionKey) return false;
      return true;
    }).slice(0, limit);
    const nextCursor = events.length > 0 ? events[events.length - 1].cursor : afterCursor;
    return { events, nextCursor };
  }

  waitForEvent(payload) {
    const afterCursor = Number.isFinite(payload.afterCursor) ? payload.afterCursor : 0;
    const sessionKey = payload.sessionKey;
    const timeoutMs = Number.isFinite(payload.timeoutMs) ? payload.timeoutMs : 30000;
    const existing = this.eventQueue.find((e) => {
      if (e.cursor <= afterCursor) return false;
      if (sessionKey && e.sessionKey !== sessionKey) return false;
      return true;
    });
    if (existing) return Promise.resolve({ event: existing });
    return new Promise((resolve) => {
      const waiter = { sessionKey, resolve: (event) => resolve({ event }) };
      if (timeoutMs > 0) {
        waiter.timer = setTimeout(() => {
          this.eventWaiters.delete(waiter);
          resolve({ event: null });
        }, timeoutMs);
      }
      this.eventWaiters.add(waiter);
    });
  }

  async fetchAttachments(payload) {
    if (!this.client) throw new Error('bridge_client_not_started');
    await this.waitForReady();
    const bridgeRequestId = crypto.randomUUID();
    const sessionKey = String(payload.sessionKey || '').trim();
    const messageId = String(payload.messageId || '').trim();
    if (!sessionKey || !messageId) throw new Error('missing_required_fields');
    this.pendingRequests.set(bridgeRequestId, { createdAt: nowIso(), sessionKey, action: 'attachments_fetch' });
    try {
      const response = await this.client.request('chat.history', { sessionKey, limit: 100 }, {
        timeoutMs: this.config.requestTimeoutMs,
      });
      const messages = response.messages || [];
      const message = messages.find((m) => m.id === messageId || m.messageId === messageId);
      if (!message) {
        log('info', 'bridge_attachments_message_not_found', { bridgeRequestId, sessionKey, messageId });
        return { bridgeRequestId, attachments: [], message: null, notFound: true };
      }
      const attachments = (message.content || []).filter((c) => c && typeof c === 'object' && c.type !== 'text');
      return { bridgeRequestId, attachments, message };
    } finally {
      this.pendingRequests.delete(bridgeRequestId);
    }
  }

  health() {
    return {
      ok: true,
      service: 'openclaw-bridge',
      startedAt: this.startedAt,
      allowWrites: this.config.allowWrites,
      aiReplyEnabled: this.config.aiReplyEnabled,
      sendMessageEnabled: this.config.allowWrites,
      gateway: {
        url: this.config.gatewayUrl,
        connected: this.connected,
        lastHello: this.lastHello,
        lastConnectError: this.lastConnectError,
        lastClose: this.lastClose,
      },
      pendingRequests: this.pendingRequests.size,
      runtime: {
        pid: process.pid,
        node: process.execPath,
        configPath: this.config.configPath,
        tokenSource: this.config.tokenSource,
        gatewayRuntimeModule: this.config.gatewayRuntimeModule,
      },
    };
  }

  async stop() {
    if (this.client) {
      try {
        await this.client.stopAndWait({ timeoutMs: 3000 });
      } catch (error) {
        log('warn', 'gateway_stop_failed', { error: error?.message || String(error) });
      }
    }
  }
}

function sendJson(res, statusCode, payload) {
  res.writeHead(statusCode, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(payload));
}

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    let body = '';
    req.on('data', (chunk) => {
      body += chunk.toString('utf8');
      if (body.length > 1024 * 1024) {
        reject(new Error('request_too_large'));
        req.destroy();
      }
    });
    req.on('end', () => {
      if (!body) return resolve({});
      try {
        resolve(JSON.parse(body));
      } catch (error) {
        reject(new Error('invalid_json'));
      }
    });
    req.on('error', reject);
  });
}

function validateSendPayload(payload) {
  const missing = [];
  if (!payload || typeof payload !== 'object') return ['body'];
  if (!payload.channel || typeof payload.channel !== 'string') missing.push('channel');
  if (!payload.target || typeof payload.target !== 'string') missing.push('target');
  if (typeof payload.body !== 'string') missing.push('body');
  return missing;
}

async function handleBridgeCall(res, fn) {
  try {
    const response = await fn();
    sendJson(res, 200, { ok: true, ...response });
  } catch (error) {
    const errorMessage = error?.message || String(error);
    let statusCode = 502;
    if (errorMessage.startsWith('bridge_not_ready')) statusCode = 503;
    if (errorMessage === 'bridge_writes_disabled' || errorMessage === 'bridge_ai_reply_disabled') statusCode = 403;
    sendJson(res, statusCode, { ok: false, error: errorMessage, details: error?.details || null });
  }
}

async function main() {
  const config = loadConfig();
  const GatewayClient = await loadGatewayClient(config.gatewayRuntimeModule);
  log('info', 'bridge_starting', {
    bridgeHost: config.bridgeHost,
    bridgePort: config.bridgePort,
    gatewayUrl: config.gatewayUrl,
    configPath: config.configPath,
    tokenSource: config.tokenSource,
    gatewayRuntimeModule: config.gatewayRuntimeModule,
    allowWrites: config.allowWrites,
    aiReplyEnabled: config.aiReplyEnabled,
    node: process.execPath,
  });
  const bridge = new BridgeRuntime(config, GatewayClient);
  bridge.start();

  const server = http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url, `http://${req.headers.host || `${config.bridgeHost}:${config.bridgePort}`}`);
      if ((req.method === 'GET' || req.method === 'POST') && url.pathname === '/health') {
        sendJson(res, 200, bridge.health());
        return;
      }
      if (req.method === 'POST' && url.pathname === SEND_PATH) {
        const payload = await readJsonBody(req);
        const missing = validateSendPayload(payload);
        if (missing.length) return sendJson(res, 400, { ok: false, error: 'missing_required_fields', missing });
        await handleBridgeCall(res, () => bridge.sendMessage(payload));
        return;
      }
      if (req.method === 'POST' && url.pathname === '/conversations-list') {
        const payload = await readJsonBody(req);
        await handleBridgeCall(res, () => bridge.listConversations(payload));
        return;
      }
      if (req.method === 'POST' && url.pathname === '/conversation-get') {
        const payload = await readJsonBody(req);
        if (!payload || !payload.sessionKey) return sendJson(res, 400, { ok: false, error: 'missing_required_fields', missing: ['sessionKey'] });
        await handleBridgeCall(res, () => bridge.getConversation(payload));
        return;
      }
      if (req.method === 'POST' && url.pathname === '/read-messages') {
        const payload = await readJsonBody(req);
        if (!payload || !payload.sessionKey) return sendJson(res, 400, { ok: false, error: 'missing_required_fields', missing: ['sessionKey'] });
        await handleBridgeCall(res, () => bridge.readMessages(payload));
        return;
      }
      if (req.method === 'POST' && url.pathname === '/ai-reply') {
        const payload = await readJsonBody(req);
        const missing = [];
        if (!payload || !payload.sessionKey) missing.push('sessionKey');
        if (!payload || typeof payload.prompt !== 'string' || !payload.prompt.trim()) missing.push('prompt');
        if (missing.length) return sendJson(res, 400, { ok: false, error: 'missing_required_fields', missing });
        await handleBridgeCall(res, () => bridge.aiReply(payload));
        return;
      }
      if (req.method === 'POST' && url.pathname === '/poll-events') {
        const payload = await readJsonBody(req);
        sendJson(res, 200, { ok: true, ...bridge.pollEvents(payload) });
        return;
      }
      if (req.method === 'POST' && url.pathname === '/wait-events') {
        const payload = await readJsonBody(req);
        const response = await bridge.waitForEvent(payload);
        sendJson(res, 200, { ok: true, ...response });
        return;
      }
      if (req.method === 'POST' && url.pathname === '/attachments-fetch') {
        const payload = await readJsonBody(req);
        if (!payload || !payload.sessionKey || !payload.messageId) {
          return sendJson(res, 400, { ok: false, error: 'missing_required_fields', missing: ['sessionKey', 'messageId'] });
        }
        await handleBridgeCall(res, () => bridge.fetchAttachments(payload));
        return;
      }
      sendJson(res, 404, { ok: false, error: 'not_found' });
    } catch (error) {
      log('error', 'bridge_http_handler_failed', { error: error?.message || String(error) });
      sendJson(res, 500, { ok: false, error: error?.message || String(error) });
    }
  });

  server.listen(config.bridgePort, config.bridgeHost, () => {
    log('info', 'bridge_http_listening', { host: config.bridgeHost, port: config.bridgePort });
  });

  const shutdown = async (signal) => {
    log('info', 'bridge_shutdown_requested', { signal });
    server.close(() => log('info', 'bridge_http_closed'));
    await bridge.stop();
    process.exit(0);
  };
  process.on('SIGINT', () => void shutdown('SIGINT'));
  process.on('SIGTERM', () => void shutdown('SIGTERM'));
}

main().catch((error) => {
  log('error', 'bridge_start_failed', { error: error?.message || String(error) });
  process.exit(1);
});
