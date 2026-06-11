import assert from "node:assert/strict";
import test from "node:test";
import { normalizeBaileysInbound } from "./inboundMapper.js";

test("normalizes direct text message", () => {
  const normalized = normalizeBaileysInbound("wa-main", {
    key: { id: "msg-1", remoteJid: "41790000000@s.whatsapp.net", fromMe: false },
    message: { conversation: "hello" },
    messageTimestamp: 1781179200
  });
  assert.equal(normalized?.external_message_id, "msg-1");
  assert.equal(normalized?.sender_phone, "+41790000000");
  assert.equal(normalized?.body_text, "hello");
  assert.equal(normalized?.message_type, "conversation");
});

test("ignores outbound, group, and empty messages", () => {
  assert.equal(normalizeBaileysInbound("wa-main", { key: { id: "x", fromMe: true } }), null);
  assert.equal(normalizeBaileysInbound("wa-main", { key: { id: "x", remoteJid: "1@g.us" }, message: { conversation: "hi" } }), null);
  assert.equal(normalizeBaileysInbound("wa-main", { key: { id: "x", remoteJid: "1@s.whatsapp.net" }, message: {} }), null);
});
