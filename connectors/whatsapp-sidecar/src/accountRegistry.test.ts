import assert from "node:assert/strict";
import test from "node:test";
import { AccountRegistry } from "./accountRegistry.js";
import type { SidecarConfig } from "./types.js";

const logger = {
  error() {},
  warn() {},
  info() {},
  debug() {},
  child() { return this; }
} as any;

function config(): SidecarConfig {
  return {
    port: 0,
    mode: "mock",
    production: false,
    sessionRoot: "/tmp/nexus-wa-registry-test",
    callbackSpoolRoot: "/tmp/nexus-wa-registry-callback-test",
    internalToken: "test-token",
    backendUrl: "http://backend.test",
    connectorKey: "connector-key",
    connectorHmacSecret: "connector-secret".repeat(4),
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

test("readiness includes durable media retrieval backlog", () => {
  const backend = {
    pendingCallbacks: () => 2,
    postInbound: async () => undefined,
    postMedia: async () => undefined,
    postStatus: async () => undefined,
    postDelivery: async () => undefined
  } as any;
  const registry = new AccountRegistry(config(), logger, backend);
  (registry.connector as any).pendingMediaDownloads = () => 3;
  registry.recordAuthoritySuccess(10_000);

  const readiness = registry.readiness(10_100);

  assert.equal(readiness.ready, true);
  assert.equal(readiness.pending_callbacks, 5);
});

test("send responses do not publish delivery callbacks before backend dispatch commits", async () => {
  const deliveries: unknown[] = [];
  const backend = {
    pendingCallbacks: () => 0,
    postInbound: async () => undefined,
    postMedia: async () => undefined,
    postStatus: async () => undefined,
    postDelivery: async (_accountId: string, payload: unknown) => {
      deliveries.push(payload);
    }
  } as any;
  const registry = new AccountRegistry(config(), logger, backend);
  await registry.start("wa-main", 1);
  (registry.connector as any).setConnected("wa-main");

  const textResult = await registry.send("wa-main", {
    idempotency_key: "text-1",
    target: "+15550000001",
    body: "hello",
    metadata: { outbound_part_id: 11 }
  });
  const mediaResult = await registry.sendMedia(
    "wa-main",
    {
      idempotency_key: "media-1",
      target: "+15550000001",
      media_kind: "image",
      media_type: "image/png",
      metadata: { outbound_part_id: 12 }
    },
    Buffer.from("image")
  );

  assert.equal(textResult.status, "sent");
  assert.equal(mediaResult.status, "sent");
  assert.deepEqual(deliveries, []);
});
