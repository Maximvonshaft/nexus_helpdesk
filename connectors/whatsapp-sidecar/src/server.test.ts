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
    production: false,
    sessionRoot: "/tmp/nexus-wa-test",
    callbackSpoolRoot: "/tmp/nexus-wa-callback-test",
    internalToken: "test-token",
    backendUrl: "http://backend.test",
    connectorKey: "connector-key",
    connectorHmacSecret: "connector-secret",
    callbackTimeoutMs: 100,
    callbackRetryIntervalMs: 1000,
    reconcileIntervalMs: 5000,
    credentialPersistenceTimeoutMs: 1000,
    qrTtlMs: 60000,
    reconnectInitialMs: 250,
    reconnectMaxMs: 1000,
    reconnectMaxAttempts: 3,
    reconnectJitter: 0,
    idempotencyTtlMs: 60000,
    logLevel: "silent",
    browserName: "NexusDesk Test",
    allowFromMeInbound: false,
    fromMeMode: "ignore",
    fromMeTestPrefix: "NEXUS_SELF_INBOUND_TEST"
  };
}

async function withServer(fn: (baseUrl: string, connector: MockConnector) => Promise<void>) {
  const connector = new MockConnector();
  const registry = {
    connector,
    backend: { pendingCallbacks: () => 0 },
    desiredAccountCount: () => 0,
    start: (accountId: string, generation?: number) => connector.start(accountId, generation),
    stop: (accountId: string) => connector.stop(accountId),
    logout: (accountId: string) => connector.logout(accountId),
    restart: (accountId: string, generation?: number) => connector.restart(accountId, generation),
    status: (accountId: string) => connector.status(accountId),
    qr: (accountId: string) => connector.status(accountId),
    requestPairingCode: (accountId: string, request: any) => connector.requestPairingCode(accountId, request),
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

test("health and readiness are public while account APIs require bearer token", async () => {
  await withServer(async (baseUrl) => {
    const health = await fetch(`${baseUrl}/healthz`);
    assert.equal(health.status, 200);
    const readiness = await fetch(`${baseUrl}/readyz`);
    assert.equal(readiness.status, 200);
    const readinessPayload = await readiness.json();
    assert.equal(readinessPayload.mode, "mock");
    assert.equal(readinessPayload.pending_callbacks, 0);
    const denied = await fetch(`${baseUrl}/accounts/wa-main/status`);
    assert.equal(denied.status, 401);
  });
});

test("start propagates generation and stop preserves credentials lifecycle", async () => {
  await withServer(async (baseUrl) => {
    const headers = {
      authorization: "Bearer test-token",
      "content-type": "application/json"
    };
    const started = await fetch(`${baseUrl}/accounts/wa-main/start`, {
      method: "POST",
      headers,
      body: JSON.stringify({ generation: 7 })
    });
    const startedPayload = await started.json();
    assert.equal(started.status, 200);
    assert.equal(startedPayload.qr_status, "pending");
    assert.equal(startedPayload.generation, 7);

    const stopped = await fetch(`${baseUrl}/accounts/wa-main/stop`, {
      method: "POST",
      headers
    });
    const stoppedPayload = await stopped.json();
    assert.equal(stopped.status, 200);
    assert.equal(stoppedPayload.listener_state, "stopped");
    assert.equal(stoppedPayload.generation, 7);
  });
});

test("pairing and send expose bounded data and stable idempotency", async () => {
  await withServer(async (baseUrl, connector) => {
    const headers = {
      authorization: "Bearer test-token",
      "content-type": "application/json"
    };
    await fetch(`${baseUrl}/accounts/wa-main/start`, {
      method: "POST",
      headers,
      body: JSON.stringify({ generation: 1 })
    });

    const paired = await fetch(`${baseUrl}/accounts/wa-main/pairing-code`, {
      method: "POST",
      headers,
      body: JSON.stringify({ phone_number: "+1 (555) 123-4567" })
    });
    const pairingPayload = await paired.json();
    assert.equal(paired.status, 200);
    assert.equal(pairingPayload.ok, true);
    assert.equal(pairingPayload.pairing_code, "12345678");
    assert.equal(pairingPayload.phone_number_suffix, "4567");
    assert.equal(JSON.stringify(pairingPayload).includes("15551234567"), false);

    connector.setConnected("wa-main");
    const sendBody = JSON.stringify({
      idempotency_key: "nexusdesk-outbound-1",
      target: "+15551234567",
      body: "hello"
    });
    const sent = await fetch(`${baseUrl}/accounts/wa-main/send`, {
      method: "POST",
      headers,
      body: sendBody
    });
    const payload = await sent.json();
    assert.equal(payload.ok, true);
    assert.equal(payload.provider_message_id, "mock-nexusdesk-outbound-1");

    const repeat = await fetch(`${baseUrl}/accounts/wa-main/send`, {
      method: "POST",
      headers,
      body: sendBody
    });
    assert.deepEqual(await repeat.json(), payload);
  });
});
