import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";
import { createHttpServer, Semaphore } from "../src/server.js";
import { loadConfig } from "../src/env.js";

test("node sidecar healthz and missing token rejection", async () => {
  const config = loadConfig({
    CODEX_APPSERVER_RUNTIME_ENABLED: "true",
    CODEX_APPSERVER_PORT: "0",
    CODEX_APPSERVER_COMMAND: "codex",
  });
  const server = createHttpServer(config);
  await listen(server);
  const address = server.address();
  assert.equal(typeof address, "object");
  const port = (address as { port: number }).port;
  try {
    const health = await request("GET", port, "/healthz");
    assert.equal(health.status, 200);
    assert.equal(health.body.ok, true);

    const reply = await request("POST", port, "/reply", { body: "hello" });
    assert.equal(reply.status, 401);
    assert.equal(reply.body.error, "codex_auth_missing");
    assert.equal(reply.body.stage_ms.terminal_wait, 0);
  } finally {
    server.close();
  }
});

test("node sidecar queue timeout is classified deterministically", async () => {
  const semaphore = new Semaphore(1);
  const release = await semaphore.acquire(10);
  try {
    await assert.rejects(
      () => semaphore.acquire(5),
      (error: any) => error?.status === 429 && error?.code === "codex_queue_timeout",
    );
  } finally {
    release();
  }
});

test("node sidecar model benchmark config is opt-in", () => {
  const config = loadConfig({});
  const benchmark = loadConfig({ CODEX_APPSERVER_MODEL: "gpt-5.4-mini" });
  const baseline = loadConfig({ CODEX_APPSERVER_PERFORMANCE_PROFILE: "baseline" });

  assert.equal(config.model, "gpt-5.5");
  assert.equal(config.maxConcurrency, 6);
  assert.equal(config.queueTimeoutMs, 750);
  assert.equal(config.performanceProfile, "webchat_fast");
  assert.equal(config.reasoningEffort, "low");
  assert.equal(config.serviceTier, "priority");
  assert.equal(benchmark.model, "gpt-5.4-mini");
  assert.equal(baseline.reasoningEffort, null);
  assert.equal(baseline.serviceTier, null);
});

function listen(server: http.Server): Promise<void> {
  return new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
}

function request(method: string, port: number, path: string, body?: unknown): Promise<{ status: number; body: any }> {
  return new Promise((resolve, reject) => {
    const raw = body === undefined ? undefined : JSON.stringify(body);
    const req = http.request(
      {
        method,
        host: "127.0.0.1",
        port,
        path,
        headers: raw ? { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(raw) } : {},
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
        res.on("end", () => {
          resolve({ status: res.statusCode || 0, body: JSON.parse(Buffer.concat(chunks).toString("utf8")) });
        });
      },
    );
    req.on("error", reject);
    if (raw) {
      req.write(raw);
    }
    req.end();
  });
}
