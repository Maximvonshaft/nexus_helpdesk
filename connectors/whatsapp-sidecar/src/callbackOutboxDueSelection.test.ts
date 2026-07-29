import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
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

test("future exhausted callbacks do not starve a later due callback", async () => {
  const root = mkdtempSync(join(tmpdir(), "nexus-callback-due-selection-"));
  let clock = 1_000;
  const outbox = new DurableCallbackOutbox(
    root,
    logger,
    "callback-due-selection-secret-" + "x".repeat(48),
    () => clock
  );
  try {
    for (let index = 0; index < 100; index += 1) {
      clock = 1_000 + index;
      outbox.enqueue({
        kind: "inbound",
        accountId: "wa-main",
        payload: { kind: "retained", index }
      });
    }

    clock = 10_000;
    for (let attempt = 0; attempt < 20; attempt += 1) {
      const result = await outbox.drain(
        async () => {
          throw new Error("backend unavailable");
        },
        100
      );
      assert.equal(result.delivered, 0);
      assert.equal(outbox.count(), 100);
      clock += 300_001;
    }

    outbox.enqueue({
      kind: "delivery",
      accountId: "wa-main",
      payload: { kind: "due" }
    });

    const observed: unknown[] = [];
    const result = await outbox.drain(async (envelope) => {
      observed.push(envelope.payload);
    }, 100);

    assert.equal(result.delivered, 1);
    assert.deepEqual(observed, [{ kind: "due" }]);
    assert.equal(outbox.count(), 100, "retained inbound callbacks remain durable");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
