import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  rmSync,
  writeFileSync
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { SessionStore } from "./sessionStore.js";
import type { SendResult } from "./types.js";

function temporaryRoot(): string {
  return mkdtempSync(join(tmpdir(), "nexus-wa-session-store-"));
}

function wait(ms: number): Promise<void> {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, ms));
}

test("does not cache retryable send failures but retains deterministic outcomes", () => {
  const root = temporaryRoot();
  try {
    const store = new SessionStore(root);
    const retryable: SendResult = {
      ok: false,
      status: "failed",
      error_code: "socket_closed",
      retryable: true
    };
    store.writeSendResult("wa-main", "media-part-1", retryable);
    assert.equal(store.readSendResult("wa-main", "media-part-1", 60_000), null);

    const deterministic: SendResult = {
      ok: false,
      status: "failed",
      error_code: "whatsapp_media_size_invalid",
      retryable: false
    };
    store.writeSendResult("wa-main", "media-part-2", deterministic);
    assert.deepEqual(
      store.readSendResult("wa-main", "media-part-2", 60_000),
      deterministic
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("invalidates retryable failures written by an older sidecar version", () => {
  const root = temporaryRoot();
  try {
    const store = new SessionStore(root);
    const accountPath = store.accountPath("wa-legacy");
    const directory = join(accountPath, "idempotency");
    mkdirSync(directory, { recursive: true, mode: 0o700 });
    const key = "legacy-retryable-media-part";
    const path = join(directory, `${createHash("sha256").update(key).digest("hex")}.json`);
    writeFileSync(
      path,
      JSON.stringify({
        created_at: Date.now(),
        result: {
          ok: false,
          status: "failed",
          error_code: "whatsapp_not_connected",
          retryable: true
        }
      }),
      { mode: 0o600 }
    );

    assert.equal(store.readSendResult("wa-legacy", key, 60_000), null);
    assert.equal(existsSync(path), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("refreshes a live account-owner lease until the holder releases it", async () => {
  const root = temporaryRoot();
  try {
    const firstStore = new SessionStore(root, 600, 50);
    const secondStore = new SessionStore(root, 600, 50);
    const firstLease = firstStore.acquireOwner("wa-main");

    await wait(900);
    assert.throws(
      () => secondStore.acquireOwner("wa-main"),
      /whatsapp_connection_owner_busy/
    );

    firstLease.release();
    const secondLease = secondStore.acquireOwner("wa-main");
    secondLease.release();
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
