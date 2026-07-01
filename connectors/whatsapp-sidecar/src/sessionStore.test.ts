import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { assertSafeAccountId, SessionStore } from "./sessionStore.js";

function withStore(fn: (store: SessionStore, root: string) => void): void {
  const root = mkdtempSync(join(tmpdir(), "nexus-wa-session-"));
  try {
    fn(new SessionStore(root), root);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

test("session store classifies empty and linked credential snapshots", () => {
  withStore((store) => {
    assert.equal(store.inspectAccount("wa-main").state, "empty");

    const accountPath = store.accountPath("wa-main");
    writeFileSync(
      join(accountPath, "creds.json"),
      JSON.stringify({
        account: { details: "present" },
        me: { id: "41798559737:18@s.whatsapp.net", name: "Maxim" },
        platform: "iphone",
        accountSyncCounter: 1,
        processedHistoryMessages: [{ key: "history" }]
      })
    );

    const snapshot = store.inspectAccount("wa-main");
    assert.equal(snapshot.state, "linked");
    assert.equal(snapshot.jid, "41798559737:18@s.whatsapp.net");
    assert.equal(snapshot.phoneNumber, "+41798559737");
    assert.equal(snapshot.platform, "iphone");
    assert.equal(snapshot.accountSyncCounter, 1);
    assert.equal(snapshot.processedHistoryMessages, 1);
  });
});

test("session store restores corrupt creds from last valid backup", () => {
  withStore((store) => {
    const accountPath = store.accountPath("wa-main");
    const goodCreds = JSON.stringify({
      account: { details: "present" },
      me: { id: "15551234567@s.whatsapp.net" }
    });
    writeFileSync(join(accountPath, "creds.json"), goodCreds);
    assert.equal(store.backupCreds("wa-main"), true);

    writeFileSync(join(accountPath, "creds.json"), "{bad-json");
    assert.equal(store.inspectAccount("wa-main").state, "corrupt");
    assert.equal(store.restoreCredsBackupIfNeeded("wa-main"), true);
    assert.equal(readFileSync(join(accountPath, "creds.json"), "utf8"), goodCreds);
    assert.equal(store.inspectAccount("wa-main").state, "linked");
  });
});

test("account ids are path-safe", () => {
  assert.equal(assertSafeAccountId("wa-main_1.2"), "wa-main_1.2");
  assert.throws(() => assertSafeAccountId("../wa-main"), /invalid_account_id/);
});
