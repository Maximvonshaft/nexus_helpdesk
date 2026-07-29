import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { DurableMediaOutbox } from "./mediaOutbox.js";

const logger = {
  error() {},
  warn() {},
  info() {},
  debug() {},
  child() { return this; }
} as any;

test("retains encrypted media bytes after retry exhaustion and later recovers", async () => {
  const root = mkdtempSync(join(tmpdir(), "nexus-media-outbox-retained-"));
  const secret = "media-outbox-secret-" + "x".repeat(48);
  let now = Date.parse("2026-07-28T00:00:00Z");
  try {
    const outbox = new DurableMediaOutbox(root, logger, secret, () => now);
    const content = Buffer.from("customer-attachment-bytes", "utf8");
    outbox.enqueue({
      accountId: "wa-main",
      externalMessageId: "message-media-1",
      mediaKind: "image",
      mediaType: "image/jpeg",
      fileName: "proof.jpg",
      content
    });

    for (let attempt = 0; attempt < 20; attempt += 1) {
      const result = await outbox.drain(async () => {
        throw new Error("backend unavailable");
      });
      assert.equal(result.delivered, 0);
      assert.equal(result.pending, 1);
      now += 5 * 60 * 1000 + 1;
    }

    assert.equal(outbox.count(), 1, "media bytes must remain durable after exhaustion");
    const spool = readdirSync(root).find((name) => name.endsWith(".media"));
    assert.ok(spool);
    const encrypted = readFileSync(join(root, spool));
    assert.equal(encrypted.includes(content), false);
    assert.equal(encrypted.includes(Buffer.from("message-media-1", "utf8")), false);

    now += 60 * 60 * 1000 + 1;
    const observed: Array<{ attempts: number; content: string }> = [];
    const recovered = await outbox.drain(async (envelope) => {
      observed.push({
        attempts: envelope.attempts,
        content: envelope.content.toString("utf8")
      });
    });
    assert.equal(recovered.delivered, 1);
    assert.equal(recovered.pending, 0);
    assert.equal(outbox.count(), 0);
    assert.deepEqual(observed, [
      { attempts: 20, content: "customer-attachment-bytes" }
    ]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("fills the batch with due uploads instead of starving behind future retries", async () => {
  const root = mkdtempSync(join(tmpdir(), "nexus-media-outbox-due-selection-"));
  const secret = "media-outbox-secret-" + "y".repeat(48);
  const now = Date.parse("2026-07-29T00:00:00Z");
  try {
    const outbox = new DurableMediaOutbox(root, logger, secret, () => now);
    for (let index = 0; index < 21; index += 1) {
      outbox.enqueue({
        accountId: "wa-main",
        externalMessageId: `message-${index.toString().padStart(2, "0")}`,
        mediaKind: "document",
        mediaType: "application/pdf",
        fileName: `proof-${index}.pdf`,
        content: Buffer.from(`bytes-${index}`, "utf8")
      });
    }

    const first = await outbox.drain(async () => {
      throw new Error("backend unavailable");
    }, 20);
    assert.equal(first.delivered, 0);
    assert.equal(first.pending, 21);

    const delivered: string[] = [];
    const second = await outbox.drain(async (envelope) => {
      delivered.push(envelope.external_message_id);
    }, 20);
    assert.equal(second.delivered, 1);
    assert.equal(second.pending, 20);
    assert.equal(delivered.length, 1);
    assert.equal(outbox.count(), 20);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
