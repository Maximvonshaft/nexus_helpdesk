import assert from "node:assert/strict";
import test from "node:test";
import { normalizeBaileysInbound } from "./inboundMapper.js";

test("normalizes direct text message", () => {
  const normalized = normalizeBaileysInbound("wa-main", {
    key: { id: "msg-1", remoteJid: "wa-contact@s.whatsapp.net", fromMe: false },
    message: { conversation: "hello" },
    messageTimestamp: 1781179200
  });
  assert.equal(normalized?.external_message_id, "msg-1");
  assert.equal(normalized?.sender_phone, null);
  assert.equal(normalized?.body_text, "hello");
  assert.equal(normalized?.message_type, "conversation");
  assert.equal(normalized?.from_me, false);
  assert.equal(normalized?.projection_mode, "visitor");
});

test("does not derive fake phone numbers from lid JIDs", () => {
  const normalized = normalizeBaileysInbound("wa-main", {
    key: { id: "msg-lid", remoteJid: "174488096354391@lid", fromMe: false },
    message: { conversation: "hello" },
    messageTimestamp: 1781179200
  });

  assert.equal(normalized?.sender_phone, null);
  assert.equal(normalized?.chat_jid, "174488096354391@lid");
});

test("ignores outbound, group, and empty messages", () => {
  assert.equal(normalizeBaileysInbound("wa-main", { key: { id: "x", remoteJid: "wa-contact@s.whatsapp.net", fromMe: true }, message: { conversation: "self" } }), null);
  assert.equal(normalizeBaileysInbound("wa-main", { key: { id: "x", remoteJid: "1@g.us" }, message: { conversation: "hi" } }), null);
  assert.equal(normalizeBaileysInbound("wa-main", { key: { id: "x", remoteJid: "status@broadcast" }, message: { conversation: "status update" } }), null);
  assert.equal(normalizeBaileysInbound("wa-main", { key: { id: "x", remoteJid: "12345@broadcast" }, message: { conversation: "broadcast" } }), null);
  assert.equal(normalizeBaileysInbound("wa-main", { key: { id: "x", remoteJid: "12345@newsletter" }, message: { conversation: "newsletter" } }), null);
  assert.equal(normalizeBaileysInbound("wa-main", { key: { id: "x", remoteJid: "wa-contact@s.whatsapp.net" }, message: {} }), null);
});

test("normalizes fromMe as store_only only when explicitly allowed", () => {
  const normalized = normalizeBaileysInbound("wa-main", {
    key: { id: "self-store", remoteJid: "wa-contact@s.whatsapp.net", fromMe: true },
    message: { conversation: "operator note" },
    messageTimestamp: 1781179200
  }, {
    allowFromMeInbound: true,
    fromMeMode: "store_only"
  });

  assert.equal(normalized?.external_message_id, "self-store");
  assert.equal(normalized?.from_me, true);
  assert.equal(normalized?.projection_mode, "store_only");
  assert.equal(normalized?.body_text, "operator note");
});

test("normalizes fromMe test visitor only with configured prefix", () => {
  const withoutPrefix = normalizeBaileysInbound("wa-main", {
    key: { id: "self-no-prefix", remoteJid: "wa-contact@s.whatsapp.net", fromMe: true },
    message: { conversation: "hello" }
  }, {
    allowFromMeInbound: true,
    fromMeMode: "test_visitor",
    fromMeTestPrefix: "SELF_TEST"
  });
  assert.equal(withoutPrefix, null);

  const withPrefix = normalizeBaileysInbound("wa-main", {
    key: { id: "self-prefix", remoteJid: "wa-contact@s.whatsapp.net", fromMe: true },
    message: { conversation: "SELF_TEST hello" }
  }, {
    allowFromMeInbound: true,
    fromMeMode: "test_visitor",
    fromMeTestPrefix: "SELF_TEST"
  });
  assert.equal(withPrefix?.from_me, true);
  assert.equal(withPrefix?.projection_mode, "test_visitor");
  assert.equal(withPrefix?.body_text, "SELF_TEST hello");
});

test("ignores fromMe self chat to prevent production echo loops", () => {
  const normalized = normalizeBaileysInbound("wa-main", {
    key: { id: "self-chat", remoteJid: "wa-contact@s.whatsapp.net", fromMe: true },
    message: { conversation: "check my parcel" }
  }, {
    allowFromMeInbound: true,
    fromMeMode: "self_chat"
  });

  assert.equal(normalized, null);
});
