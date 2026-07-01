import assert from "node:assert/strict";
import test from "node:test";
import { BackendClient } from "./backendClient.js";
import type { SidecarConfig } from "./types.js";

function config(): SidecarConfig {
  return {
    port: 0,
    mode: "mock",
    sessionRoot: "/tmp/nexus-wa-test",
    autoStartAccounts: [],
    internalToken: "test-token",
    backendUrl: "http://backend.test",
    connectorKey: "connector-key",
    connectorHmacSecret: "connector-secret",
    callbackTimeoutMs: 100,
    logLevel: "silent",
    baileysLogLevel: "silent",
    browserPlatform: "mock",
    browserName: "NexusDesk Test",
    browserVersion: "0.1.0",
    keepAliveIntervalMs: 25_000,
    connectTimeoutMs: 60_000,
    defaultQueryTimeoutMs: 60_000,
    operationTimeoutMs: 60_000,
    qrTtlMs: 120_000,
    reconnectBaseDelayMs: 10,
    reconnectMaxDelayMs: 100,
    reconnectMaxAttempts: 3,
    allowFromMeInbound: false,
    fromMeMode: "ignore",
    fromMeTestPrefix: "NEXUS_SELF_INBOUND_TEST"
  };
}

test("backend callback failures include actionable status fields", async () => {
  const originalFetch = globalThis.fetch;
  const warnings: Array<{ payload: Record<string, unknown>; message: string }> = [];
  globalThis.fetch = (async () => new Response("missing", { status: 404 })) as typeof fetch;
  const logger = {
    warn(payload: Record<string, unknown>, message: string) {
      warnings.push({ payload, message });
    }
  };

  try {
    const client = new BackendClient(config(), logger as any);
    await assert.rejects(
      () => client.postStatus("wa-test", { account_id: "wa-test", status: "qr_pending" }),
      /backend_callback_failed:404/
    );
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(warnings.length, 1);
  assert.equal(warnings[0].message, "backend_callback_failed");
  assert.equal(warnings[0].payload.account_id, "wa-test");
  assert.equal(warnings[0].payload.path, "/api/integrations/whatsapp/native/status");
  assert.equal(warnings[0].payload.error_code, "backend_callback_http_error");
  assert.equal(warnings[0].payload.error_message, "backend_callback_failed:404");
  assert.equal(warnings[0].payload.status_code, 404);
});
