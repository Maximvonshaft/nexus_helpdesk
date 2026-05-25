import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";
import { createHttpServer } from "../src/server.js";
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
  } finally {
    server.close();
  }
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
