import http, { type IncomingMessage, type ServerResponse } from "node:http";
import { loadConfig, type RuntimeConfig } from "./env.js";
import { RuntimeError, normalizeError } from "./errors.js";
import { ClientCache, clientCacheKey } from "./client-cache.js";
import { loginAccount } from "./account-login.js";
import { deadlineFromHeader, remainingMs } from "./deadline.js";
import { StageTimer } from "./metrics.js";
import { parseStrictReply, validateReplyRequest } from "./reply-contract.js";
import { redact } from "./redaction.js";
import { runEphemeralThread } from "./thread-runner.js";

type QueueWaiter = () => void;

export class Semaphore {
  private active = 0;
  private readonly waiters: QueueWaiter[] = [];

  constructor(private readonly max: number) {}

  async acquire(timeoutMs: number): Promise<() => void> {
    if (this.active < this.max) {
      this.active += 1;
      return () => this.release();
    }
    return await new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.remove(waiter);
        reject(new RuntimeError(429, "codex_queue_timeout", "codex_queue_timeout", "queue"));
      }, timeoutMs);
      const waiter = () => {
        clearTimeout(timer);
        this.active += 1;
        resolve(() => this.release());
      };
      this.waiters.push(waiter);
    });
  }

  private release(): void {
    this.active = Math.max(0, this.active - 1);
    const next = this.waiters.shift();
    if (next) {
      next();
    }
  }

  private remove(waiter: QueueWaiter): void {
    const index = this.waiters.indexOf(waiter);
    if (index >= 0) {
      this.waiters.splice(index, 1);
    }
  }
}

export function createHttpServer(config: RuntimeConfig = loadConfig()): http.Server {
  const cache = new ClientCache(config);
  const semaphore = new Semaphore(config.maxConcurrency);

  return http.createServer(async (req, res) => {
    try {
      if (req.method === "GET" && req.url === "/healthz") {
        return sendJson(res, 200, healthPayload(config));
      }
      if (req.method === "GET" && req.url === "/readyz") {
        return sendJson(res, config.enabled ? 200 : 503, readyPayload(config));
      }
      if (req.method === "POST" && req.url === "/reply") {
        return await handleReply(req, res, config, cache, semaphore);
      }
      return sendJson(res, 404, { ok: false, error: "not_found" });
    } catch (error) {
      const normalized = normalizeError(error);
      return sendJson(res, normalized.status, { ok: false, error: normalized.code });
    }
  });
}

async function handleReply(
  req: IncomingMessage,
  res: ServerResponse,
  config: RuntimeConfig,
  cache: ClientCache,
  semaphore: Semaphore,
): Promise<void> {
  if (!config.enabled) {
    throw new RuntimeError(503, "codex_runtime_error", "runtime_disabled");
  }
  const timer = new StageTimer();
  const deadlineMs = deadlineFromHeader(req.headers["x-nexus-request-deadline-ms"], config.replyTimeoutMs);
  const queueStarted = Date.now();
  const release = await semaphore.acquire(Math.min(config.queueTimeoutMs, remainingMs(deadlineMs)));
  timer.set("queue", Date.now() - queueStarted);
  let cacheState: "hit" | "miss" = "miss";
  try {
    const request = validateReplyRequest(await readBody(req));
    const key = clientCacheKey({
      tenantId: request.tenant_id || "default",
      login: request.login,
      model: config.model,
      runtimeStartOptionsHash: config.runtimeStartOptionsHash,
    });
    const lookup = await timer.measure("client_lookup", () =>
      cache.getOrCreate(key, Math.min(remainingMs(deadlineMs), config.replyTimeoutMs)),
    );
    cacheState = lookup.cache;
    timer.set("appserver_start", lookup.appserverStartMs);
    timer.set("initialize", lookup.initializeMs);
    await timer.measure("login", () => loginAccount(lookup.client, request.login, Math.min(remainingMs(deadlineMs), 3000)));
    const run = await runEphemeralThread(lookup.client, config, request, Math.min(remainingMs(deadlineMs), config.replyTimeoutMs));
    timer.set("thread_start", run.threadStartMs);
    timer.set("turn_start", run.turnStartMs);
    timer.set("terminal_wait", run.terminalWaitMs);
    const parsed = await timer.measure("parse", async () => parseStrictReply(run.assistantText));
    const stages = timer.snapshot();
    return sendJson(
      res,
      200,
      { ...parsed, stage_ms: stages },
      {
        "X-Nexus-Codex-Backend": "nexus_codex_appserver_runtime",
        "X-Nexus-Codex-Elapsed-Ms": String(stages.total),
        "X-Nexus-Codex-Client-Cache": cacheState,
        "X-Nexus-Codex-Thread-Mode": "ephemeral",
        "X-Nexus-Codex-Upstream-SHA": "none",
      },
    );
  } catch (error) {
    const normalized = normalizeError(error);
    const stages = timer.snapshot();
    return sendJson(
      res,
      normalized.status,
      { ok: false, error: normalized.code, error_stage: normalized.stage ?? null, stage_ms: stages },
      {
        "X-Nexus-Codex-Backend": "nexus_codex_appserver_runtime",
        "X-Nexus-Codex-Elapsed-Ms": String(stages.total),
        "X-Nexus-Codex-Client-Cache": cacheState,
        "X-Nexus-Codex-Thread-Mode": "ephemeral",
        "X-Nexus-Codex-Upstream-SHA": "none",
      },
    );
  } finally {
    release();
  }
}

async function readBody(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    if (Buffer.concat(chunks).length > 128 * 1024) {
      throw new RuntimeError(400, "codex_request_invalid", "request_too_large");
    }
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new RuntimeError(400, "codex_request_invalid", "invalid_json");
  }
}

function healthPayload(config: RuntimeConfig): Record<string, unknown> {
  return {
    ok: true,
    service: "nexus-codex-appserver-runtime",
    version: "0.1.0",
    git_sha: config.release.gitSha,
    image_tag: config.release.imageTag,
    app_version: config.release.appVersion,
    build_time: config.release.buildTime,
  };
}

function readyPayload(config: RuntimeConfig): Record<string, unknown> {
  return {
    ...healthPayload(config),
    ok: config.enabled,
    enabled: config.enabled,
    model: config.model,
    performance_profile: config.performanceProfile,
    service_tier: config.serviceTier,
    reasoning_effort: config.reasoningEffort,
    thread_mode: config.threadMode,
    runtime_start_options_hash: config.runtimeStartOptionsHash,
  };
}

function sendJson(
  res: ServerResponse,
  status: number,
  payload: Record<string, unknown>,
  headers: Record<string, string> = {},
): void {
  const body = JSON.stringify(redact(payload));
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "Content-Length": Buffer.byteLength(body),
    ...headers,
  });
  res.end(body);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const config = loadConfig();
  createHttpServer(config).listen(config.port, config.host, () => {
    process.stdout.write(JSON.stringify({ service: "nexus-codex-appserver-runtime", port: config.port }) + "\n");
  });
}
