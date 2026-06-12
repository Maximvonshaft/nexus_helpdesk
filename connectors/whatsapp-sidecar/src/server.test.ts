import assert from "node:assert/strict";
import { once } from "node:events";
import test from "node:test";
import { createLogger } from "./logger.js";
import { MockConnector } from "./mockConnector.js";
import { createSidecarServer } from "./server.js";
import type { SidecarConfig } from "./types.js";

function config(): SidecarConfig {
  return {
    port: 0,
    mode: "mock",
    sessionRoot: "/tmp/nexus-wa-test",
    internalToken: "test-token",
    backendUrl: "http://backend.test",
    connectorKey: "connector-key",
    connectorHmacSecret: "connector-secret",
    callbackTimeoutMs: 100,
    logLevel: "silent",
    allowFromMeInbound: false,
    fromMeMode: "ignore",
    fromMeTestPrefix: "NEXUS_SELF_INBOUND_TEST"
  };
}

async function withServer(fn: (baseUrl: string, connector: MockConnector) => Promise<void>) {
  const connector = new MockConnector();
  const registry = {
    connector,
    start: (accountId: string) => connector.start(accountId),
    logout: (accountId: string) => connector.logout(accountId),
    restart: (accountId: string) => connector.restart(accountId),
    status: (accountId: string) => connector.status(accountId),
    qr: (accountId: string) => connector.status(accountId),
    send: (accountId: string, request: any) => connector.send(accountId, request)
  };
  const server = createSidecarServer(config(), createLogger("silent"), registry as any);
  server.listen(0);
  await once(server, "listening");
  const address = server.address();
  assert.ok(address && typeof address === "object");
  try {
    await fn(`http://127.0.0.1:${address.port}`, connector);
  } finally {
    server.close();
  }
}

test("healthz is public and account APIs require bearer token", async () => {
  await withServer(async (baseUrl) => {
    const health = await fetch(`${baseUrl}/healthz`);
    assert.equal(health.status, 200);
    const denied = await fetch(`${baseUrl}/accounts/wa-main/status`);
    assert.equal(denied.status, 401);
  });
});

test("start, status, qr, and send expose stable sidecar contract", async () => {
  await withServer(async (baseUrl, connector) => {
    const headers = { authorization: "Bearer test-token" };
    const started = await fetch(`${baseUrl}/accounts/wa-main/start`, { method: "POST", headers });
    assert.equal(started.status, 200);
    assert.equal((await started.json()).qr_status, "pending");

    connector.setConnected("wa-main");
    const sent = await fetch(`${baseUrl}/accounts/wa-main/send`, {
      method: "POST",
      headers: { ...headers, "content-type": "application/json" },
      body: JSON.stringify({
        idempotency_key: "nexusdesk-outbound-1",
        target: "wa-contact",
        body: "hello"
      })
    });
    const payload = await sent.json();
    assert.equal(payload.ok, true);
    assert.equal(payload.provider_message_id, "mock-nexusdesk-outbound-1");

    const repeat = await fetch(`${baseUrl}/accounts/wa-main/send`, {
      method: "POST",
      headers: { ...headers, "content-type": "application/json" },
      body: JSON.stringify({
        idempotency_key: "nexusdesk-outbound-1",
        target: "wa-contact",
        body: "hello"
      })
    });
    assert.deepEqual(await repeat.json(), payload);
  });
});
