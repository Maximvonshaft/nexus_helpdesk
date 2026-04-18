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
    gatewayScopes: (process.env.OPENCLAW_BRIDGE_GATEWAY_SCOPES || 'operator.read,operator.write')
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean),
    token,
    tokenSource: process.env.OPENCLAW_GATEWAY_TOKEN ? 'env.OPENCLAW_GATEWAY_TOKEN' : configPath,
    configPath,
    gatewayRuntimeModule:
      process.env.OPENCLAW_GATEWAY_RUNTIME_MODULE || DEFAULT_GATEWAY_RUNTIME,
    connectChallengeTimeoutMs: parseIntEnv('OPENCLAW_BRIDGE_CONNECT_CHALLENGE_TIMEOUT_MS', 8000),
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
    if (this.connected) {
      return Promise.resolve();
    }
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
    if (!this.client) {
      throw new Error('bridge_client_not_started');
    }
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

    log('info', 'bridge_send_dispatch', {
      bridgeRequestId,
      channel: params.channel,
      target: params.to,
      hasSessionKey: Boolean(params.sessionKey),
      pendingCount: this.pendingRequests.size,
    });

    try {
      const result = await this.client.request('send', params, { timeoutMs: this.config.requestTimeoutMs });
      log('info', 'bridge_send_success', {
        bridgeRequestId,
        channel: params.channel,
        target: params.to,
        pendingCount: this.pendingRequests.size - 1,
      });
      return { bridgeRequestId, idempotencyKey, result };
    } catch (error) {
      log('warn', 'bridge_send_failed', {
        bridgeRequestId,
        channel: params.channel,
        target: params.to,
        error: error?.message || String(error),
        details: error?.details || null,
      });
      throw error;
    } finally {
      this.pendingRequests.delete(bridgeRequestId);
    }
  }

  async getConversation(payload) {
    if (!this.client) {
      throw new Error('bridge_client_not_started');
    }
    await this.waitForReady();

    const bridgeRequestId = crypto.randomUUID();
    const sessionKey = String(payload.sessionKey || '').trim();
    if (!sessionKey) throw new Error('missing_sessionKey');

    this.pendingRequests.set(bridgeRequestId, {
      createdAt: nowIso(),
      sessionKey,
      action: 'conversation_get',
    });

    log('info', 'bridge_conversation_get_dispatch', { bridgeRequestId, sessionKey });

    try {
      const response = await this.client.request('sessions.list', {
        limit: 500,
        includeLastMessage: true,
      }, { timeoutMs: this.config.requestTimeoutMs });

      const sessions = response.sessions || [];
      const conversation = sessions.find((s) => s.key === sessionKey || s.id === sessionKey) || null;

      log('info', 'bridge_conversation_get_success', { bridgeRequestId, sessionKey, found: Boolean(conversation) });
      return { bridgeRequestId, conversation };
    } catch (error) {
      log('warn', 'bridge_conversation_get_failed', {
        bridgeRequestId,
        sessionKey,
        error: error?.message || String(error),
        details: error?.details || null,
      });
      throw error;
    } finally {
      this.pendingRequests.delete(bridgeRequestId);
    }
  }

  async readMessages(payload) {
    if (!this.client) {
      throw new Error('bridge_client_not_started');
    }
    await this.waitForReady();

    const bridgeRequestId = crypto.randomUUID();
    const sessionKey = String(payload.sessionKey || '').trim();
    if (!sessionKey) throw new Error('missing_sessionKey');
    const limit = Number.isFinite(payload.limit) ? payload.limit : 20;

    this.pendingRequests.set(bridgeRequestId, {
      createdAt: nowIso(),
      sessionKey,
      action: 'messages_read',
    });

    log('info', 'bridge_messages_read_dispatch', { bridgeRequestId, sessionKey, limit });

    try {
      const response = await this.client.request('chat.history', {
        sessionKey,
        limit,
      }, { timeoutMs: this.config.requestTimeoutMs });

      const messages = response.messages || [];

      log('info', 'bridge_messages_read_success', { bridgeRequestId, sessionKey, count: messages.length });
      return { bridgeRequestId, messages };
    } catch (error) {
      log('warn', 'bridge_messages_read_failed', {
        bridgeRequestId,
        sessionKey,
        error: error?.message || String(error),
        details: error?.details || null,
      });
      throw error;
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
      // If client sends a cursor from the future (e.g. MCP cursor) or an entirely different timeline,
      // it might block forever. To be safer, we rely strictly on the bridge's internal monotonic time.
      // But if afterCursor is 0 or somehow smaller, it works.
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

    this.pendingRequests.set(bridgeRequestId, {
      createdAt: nowIso(),
      sessionKey,
      action: 'attachments_fetch',
    });

    log('info', 'bridge_attachments_fetch_dispatch', { bridgeRequestId, sessionKey, messageId });

    try {
      const response = await this.client.request('chat.history', {
        sessionKey,
        limit: 100,
      }, { timeoutMs: this.config.requestTimeoutMs });

      const messages = response.messages || [];
      const message = messages.find((m) => m.id === messageId || m.messageId === messageId);
      
      if (!message) {
        log('warn', 'bridge_attachments_fetch_failed', { bridgeRequestId, sessionKey, messageId, reason: 'message_not_found' });
        throw new Error('message_not_found');
      }

      const attachments = (message.content || []).filter((c) => c && typeof c === 'object' && c.type !== 'text');

      log('info', 'bridge_attachments_fetch_success', { bridgeRequestId, sessionKey, messageId, attachmentsCount: attachments.length });
      return { bridgeRequestId, attachments, message };
    } catch (error) {
      log('warn', 'bridge_attachments_fetch_failed', {
        bridgeRequestId,
        sessionKey,
        messageId,
        error: error?.message || String(error),
      });
      throw error;
    } finally {
      this.pendingRequests.delete(bridgeRequestId);
    }
  }

  health() {
    return {
      ok: true,
      service: 'openclaw-bridge',
      startedAt: this.startedAt,
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
      if (!body) {
        resolve({});
        return;
      }
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
  if (!payload || typeof payload !== 'object') {
    return ['body'];
  }
  if (!payload.channel || typeof payload.channel !== 'string') missing.push('channel');
  if (!payload.target || typeof payload.target !== 'string') missing.push('target');
  if (typeof payload.body !== 'string') missing.push('body');
  return missing;
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
      if (req.method === 'POST' && url.pathname === '/send-message') {
        const payload = await readJsonBody(req);
        const missing = validateSendPayload(payload);
        if (missing.length) {
          sendJson(res, 400, { ok: false, error: 'missing_required_fields', missing });
          return;
        }
        try {
          const response = await bridge.sendMessage(payload);
          sendJson(res, 200, { ok: true, ...response });
        } catch (error) {
          const errorMessage = error?.message || String(error);
          const statusCode = errorMessage.startsWith('bridge_not_ready') ? 503 : 502;
          sendJson(res, statusCode, {
            ok: false,
            error: errorMessage,
            details: error?.details || null,
          });
        }
        return;
      }

      if (req.method === 'POST' && url.pathname === '/conversation-get') {
        const payload = await readJsonBody(req);
        if (!payload || !payload.sessionKey) {
          sendJson(res, 400, { ok: false, error: 'missing_required_fields', missing: ['sessionKey'] });
          return;
        }
        try {
          const response = await bridge.getConversation(payload);
          sendJson(res, 200, { ok: true, ...response });
        } catch (error) {
          const errorMessage = error?.message || String(error);
          const statusCode = errorMessage.startsWith('bridge_not_ready') ? 503 : 502;
          sendJson(res, statusCode, {
            ok: false,
            error: errorMessage,
            details: error?.details || null,
          });
        }
        return;
      }

      if (req.method === 'POST' && url.pathname === '/read-messages') {
        const payload = await readJsonBody(req);
        if (!payload || !payload.sessionKey) {
          sendJson(res, 400, { ok: false, error: 'missing_required_fields', missing: ['sessionKey'] });
          return;
        }
        try {
          const response = await bridge.readMessages(payload);
          sendJson(res, 200, { ok: true, ...response });
        } catch (error) {
          const errorMessage = error?.message || String(error);
          const statusCode = errorMessage.startsWith('bridge_not_ready') ? 503 : 502;
          sendJson(res, statusCode, {
            ok: false,
            error: errorMessage,
            details: error?.details || null,
          });
        }
        return;
      }

      if (req.method === 'POST' && url.pathname === '/poll-events') {
        const payload = await readJsonBody(req);
        try {
          const response = bridge.pollEvents(payload);
          sendJson(res, 200, { ok: true, ...response });
        } catch (error) {
          sendJson(res, 500, { ok: false, error: error?.message || String(error) });
        }
        return;
      }

      if (req.method === 'POST' && url.pathname === '/wait-events') {
        const payload = await readJsonBody(req);
        try {
          const response = await bridge.waitForEvent(payload);
          sendJson(res, 200, { ok: true, ...response });
        } catch (error) {
          sendJson(res, 500, { ok: false, error: error?.message || String(error) });
        }
        return;
      }

      if (req.method === 'POST' && url.pathname === '/attachments-fetch') {
        const payload = await readJsonBody(req);
        if (!payload || !payload.sessionKey || !payload.messageId) {
          sendJson(res, 400, { ok: false, error: 'missing_required_fields', missing: ['sessionKey', 'messageId'] });
          return;
        }
        try {
          const response = await bridge.fetchAttachments(payload);
          sendJson(res, 200, { ok: true, ...response });
        } catch (error) {
          const errorMessage = error?.message || String(error);
          const statusCode = errorMessage.startsWith('bridge_not_ready') ? 503 : 502;
          sendJson(res, statusCode, {
            ok: false,
            error: errorMessage,
            details: error?.details || null,
          });
        }
        return;
      }

      sendJson(res, 404, { ok: false, error: 'not_found' });
    } catch (error) {
      log('error', 'bridge_http_handler_failed', { error: error?.message || String(error) });
      sendJson(res, 500, { ok: false, error: error?.message || String(error) });
    }
  });

  server.listen(config.bridgePort, config.bridgeHost, () => {
    log('info', 'bridge_http_listening', {
      host: config.bridgeHost,
      port: config.bridgePort,
    });
  });

  const shutdown = async (signal) => {
    log('info', 'bridge_shutdown_requested', { signal });
    server.close(() => {
      log('info', 'bridge_http_closed');
    });
    await bridge.stop();
    process.exit(0);
  };

  process.on('SIGINT', () => {
    void shutdown('SIGINT');
  });
  process.on('SIGTERM', () => {
    void shutdown('SIGTERM');
  });
}

main().catch((error) => {
  log('error', 'bridge_start_failed', { error: error?.message || String(error) });
  process.exit(1);
});
