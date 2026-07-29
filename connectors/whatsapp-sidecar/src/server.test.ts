import assert from "node:assert/strict";
import { once } from "node:events";
import test from "node:test";
import { createLogger } from "./logger.js";
import { MockConnector } from "./mockConnector.js";
import { createSidecarServer } from "./server.js";
import type { RegistryReadiness } from "./accountRegistry.js";
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

function readiness(
  status: RegistryReadiness["status"],
  ready: boolean
): RegistryReadiness {
  return {
    ready,
    status,
    authority_established: status !== "starting",
    authority_fresh: ready,
    last_authority_success_at: ready ? new Date().toISOString() : null,
    last_authority_failure_at: status === "degraded" || status === "not_ready"
      ? new Date().toISOString()
      : null,
    last_authority_error_code: status === "degraded" || status === "not_ready"
      ? "backend_authority_unavailable"
      : null,
    desired_accounts: 0,
    pending_callbacks: 0
  };
}

async function withServer(
  fn: (
    baseUrl: string,
    connector: MockConnector,
    registry: { setReadiness: (value: RegistryReadiness) => void }
  ) => Promise<void>
) {
  const connector = new MockConnector();
  let observedReadiness = readiness("starting", false);
  const registry = {
    connector,
    backend: { pendingCallbacks: () => 0 },
    desiredAccountCount: () => 0,
    readiness: () => observedReadiness,
    setReadiness: (value: RegistryReadiness) => {
      observedReadiness = value;
    },
    start: (accountId: string, generation?: number) => connector.start(accountId, generation),
    stop: (accountId: string) => connector.stop(accountId),
    logout: (accountId: string) => connector.logout(accountId),
    restart: (accountId: string, generation?: number) => connector.restart(accountId, generation),
    status: (accountId: string) => connector.status(accountId),
    qr: (accountId: string) => connector.status(accountId),
    requestPairingCode: (accountId: string, request: any) => connector.requestPairingCode(accountId, request),
    send: (accountId: string, request: any) => connector.send(accountId, request),
    sendMedia: (accountId: string, request: any, content: Buffer) => connector.sendMedia(accountId, request, content)
  };
  const server = createSidecarServer(config(), createLogger("silent"), registry as any);
  server.listen(0);
  await once(server, "listening");
  const address = server.address();
  assert.ok(address && typeof address === "object");
  try {
    await fn(`http://127.0.0.1:${address.port}`, connector, registry);
  } finally {
    server.close();
  }
}

test("health is public while readiness follows backend desired-state authority", async () => {
  await withServer(async (baseUrl, _connector, registry) => {
    const health = await fetch(`${baseUrl}/healthz`);
    assert.equal(health.status, 200);

    const starting = await fetch(`${baseUrl}/readyz`);
    assert.equal(starting.status, 503);
    assert.equal((await starting.json()).status, "starting");

    registry.setReadiness(readiness("ready", true));
    const ready = await fetch(`${baseUrl}/readyz`);
    assert.equal(ready.status, 200);
    const readyPayload = await ready.json();
    assert.equal(readyPayload.mode, "mock");
    assert.equal(readyPayload.authority_fresh, true);

    registry.setReadiness(readiness("degraded", true));
    const degraded = await fetch(`${baseUrl}/readyz`);
    assert.equal(degraded.status, 200);
    assert.equal((await degraded.json()).status, "degraded");

    registry.setReadiness(readiness("not_ready", false));
    const stale = await fetch(`${baseUrl}/readyz`);
    assert.equal(stale.status, 503);
    assert.equal((await stale.json()).last_authority_error_code, "backend_authority_unavailable");

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
