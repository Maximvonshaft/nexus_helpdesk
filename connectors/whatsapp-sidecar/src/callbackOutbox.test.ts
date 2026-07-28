import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { DurableCallbackOutbox } from "./callbackOutbox.js";

const logger = {
  error() {},
  warn() {},
  info() {},
  debug() {},
  child() { return this; }
} as any;

test("retains encrypted inbound callback after retry exhaustion and later recovers", async () => {
  const root = mkdtempSync(join(tmpdir(), "nexus-callback-outbox-"));
  const secret = "callback-outbox-secret-" + "x".repeat(48);
  let now = Date.parse("2026-07-28T00:00:00Z");
  try {
    const outbox = new DurableCallbackOutbox(root, logger, secret, () => now);
    outbox.enqueue({
      kind: "inbound",
      accountId: "wa-main",
      payload: { external_message_id: "message-1", body: "customer message" }
    });

    for (let attempt = 0; attempt < 20; attempt += 1) {
      const result = await outbox.drain(async () => {
        throw new Error("backend unavailable");
      });
      assert.equal(result.delivered, 0);
      assert.equal(result.pending, 1);
      now += 5 * 60 * 1000 + 1;
    }

    assert.equal(outbox.count(), 1, "inbound payload must remain durable after exhaustion");
    const spool = readdirSync(root).find((name) => name.endsWith(".json"));
    assert.ok(spool);
    const encrypted = readFileSync(join(root, spool));
    assert.equal(encrypted.includes(Buffer.from("customer message", "utf8")), false);
    assert.equal(encrypted.includes(Buffer.from("message-1", "utf8")), false);

    now += 60 * 60 * 1000 + 1;
    const observed: unknown[] = [];
    const recovered = await outbox.drain(async (envelope) => {
      observed.push(envelope.payload);
    });
    assert.equal(recovered.delivered, 1);
    assert.equal(outbox.count(), 0);
    assert.deepEqual(observed, [
      { external_message_id: "message-1", body: "customer message" }
    ]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("terminal delivery callback remains bounded and is removed", async () => {
  const root = mkdtempSync(join(tmpdir(), "nexus-delivery-outbox-"));
  const secret = "callback-outbox-secret-" + "y".repeat(48);
  let now = Date.parse("2026-07-28T00:00:00Z");
  try {
    const outbox = new DurableCallbackOutbox(root, logger, secret, () => now);
    outbox.enqueue({
      kind: "delivery",
      accountId: "wa-main",
      payload: { provider_message_id: "delivery-1", status: "read" }
    });
    for (let attempt = 0; attempt < 20; attempt += 1) {
      await outbox.drain(async () => {
        throw new Error("backend unavailable");
      });
      now += 5 * 60 * 1000 + 1;
    }
    assert.equal(outbox.count(), 0);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
