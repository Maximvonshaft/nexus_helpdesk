import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { proto } from "@whiskeysockets/baileys";
import { DurableMediaDownloadOutbox } from "./mediaDownloadOutbox.js";

const logger = {
  error() {},
  warn() {},
  info() {},
  debug() {},
  child() { return this; }
} as any;

function rawMessage(id: string): proto.IWebMessageInfo {
  return proto.WebMessageInfo.create({
    key: {
      id,
      remoteJid: "15551234567@s.whatsapp.net",
      fromMe: false
    },
    message: {
      imageMessage: {
        url: "https://mmg.whatsapp.net/example",
        mimetype: "image/jpeg",
        fileLength: 10
      }
    }
  });
}

test("persists encrypted media download work across process restart", async () => {
  const root = mkdtempSync(join(tmpdir(), "nexus-media-download-"));
  const secret = "media-download-secret-" + "x".repeat(48);
  try {
    const first = new DurableMediaDownloadOutbox(root, logger, secret);
    first.enqueue({
      accountId: "wa-main",
      externalMessageId: "message-1",
      mediaKind: "image",
      mediaType: "image/jpeg",
      fileName: "photo.jpg",
      rawMessage: rawMessage("message-1")
    });
    const spool = readdirSync(root).find((name) => name.endsWith(".download"));
    assert.ok(spool);
    const bytes = readFileSync(join(root, spool));
    assert.equal(bytes.includes(Buffer.from("message-1", "utf8")), false);
    assert.equal(bytes.includes(Buffer.from("mmg.whatsapp.net", "utf8")), false);

    const firstAttemptAt = Date.now() + 1_000;
    const failed = await first.drainAccount(
      "wa-main",
      async () => {
        throw new Error("transient provider failure");
      },
      20,
      firstAttemptAt
    );
    assert.equal(failed.delivered, 0);
    assert.equal(failed.pending, 1);
    assert.equal(first.count(), 1);

    first.enqueue({
      accountId: "wa-main",
      externalMessageId: "message-1",
      mediaKind: "image",
      mediaType: "image/jpeg",
      fileName: "duplicate.jpg",
      rawMessage: rawMessage("message-1")
    });
    assert.equal(first.count(), 1, "duplicate upsert must not reset the pending task");

    const restarted = new DurableMediaDownloadOutbox(root, logger, secret);
    const observed: Array<{ id: string; attempts: number; fileName: string | null }> = [];
    const delivered = await restarted.drainAccount(
      "wa-main",
      async (envelope) => {
        observed.push({
          id: String(envelope.raw_message.key?.id || ""),
          attempts: envelope.attempts,
          fileName: envelope.file_name
        });
      },
      20,
      firstAttemptAt + 10_000
    );
    assert.deepEqual(observed, [
      { id: "message-1", attempts: 1, fileName: "photo.jpg" }
    ]);
    assert.equal(delivered.delivered, 1);
    assert.equal(restarted.count(), 0);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("preserves non-retryable media download failures as dead-letter evidence", async () => {
  const root = mkdtempSync(join(tmpdir(), "nexus-media-download-dead-"));
  const secret = "media-download-secret-" + "y".repeat(48);
  try {
    const outbox = new DurableMediaDownloadOutbox(root, logger, secret);
    outbox.enqueue({
      accountId: "wa-main",
      externalMessageId: "message-dead",
      mediaKind: "image",
      mediaType: "image/jpeg",
      rawMessage: rawMessage("message-dead")
    });
    const error = Object.assign(new Error("media too large"), { retryable: false });
    const result = await outbox.drainAccount(
      "wa-main",
      async () => {
        throw error;
      },
      20,
      Date.now() + 1_000
    );
    assert.equal(result.dead, 1);
    assert.equal(outbox.count(), 0);
    assert.equal(outbox.countDead(), 1);
    outbox.enqueue({
      accountId: "wa-main",
      externalMessageId: "message-dead",
      mediaKind: "image",
      mediaType: "image/jpeg",
      rawMessage: rawMessage("message-dead")
    });
    assert.equal(outbox.count(), 0, "duplicate upsert must not resurrect dead work");
    assert.equal(outbox.countDead(), 1);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
