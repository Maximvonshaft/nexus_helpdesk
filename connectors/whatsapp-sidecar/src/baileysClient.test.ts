import assert from "node:assert/strict";
import test from "node:test";
import { phoneJidFromAccountSnapshot, projectSelfTestInboundToPhoneJid, targetToWhatsAppJid } from "./baileysClient.js";

test("normalizes send targets without corrupting WhatsApp JIDs", () => {
  assert.equal(targetToWhatsAppJid("+1 (555) 123-4567"), "15551234567@s.whatsapp.net");
  assert.equal(targetToWhatsAppJid("15551234567@s.whatsapp.net"), "15551234567@s.whatsapp.net");
  assert.equal(targetToWhatsAppJid("174488096354391@lid"), "174488096354391@lid");
  assert.equal(targetToWhatsAppJid("174488096354391:19@lid"), "174488096354391:19@lid");
  assert.equal(targetToWhatsAppJid("123@g.us"), null);
  assert.equal(targetToWhatsAppJid("status@broadcast"), null);
  assert.equal(targetToWhatsAppJid("not-a-whatsapp@example.com"), null);
});

test("projects fromMe self-test inbound to the account phone JID", () => {
  assert.equal(phoneJidFromAccountSnapshot({ phone_number: "+41 79 855 97 37", jid: null }), "41798559737@s.whatsapp.net");
  assert.equal(phoneJidFromAccountSnapshot({ phone_number: null, jid: "41798559737:19@s.whatsapp.net" }), "41798559737@s.whatsapp.net");

  const projected = projectSelfTestInboundToPhoneJid(
    {
      account_id: "wa-main",
      external_message_id: "self-1",
      chat_jid: "174488096354391@lid",
      sender_jid: "174488096354391@lid",
      sender_phone: null,
      message_type: "conversation",
      body_text: "/ai hello",
      raw_payload: { key: { remoteJid: "174488096354391@lid" } },
      received_at: "2026-07-03T13:00:00Z",
      from_me: true,
      projection_mode: "test_visitor",
      self_echo_test_prefix: "/ai "
    },
    { phone_number: "+41798559737", jid: "41798559737:19@s.whatsapp.net" }
  );

  assert.equal(projected.chat_jid, "41798559737@s.whatsapp.net");
  assert.equal(projected.sender_jid, "41798559737@s.whatsapp.net");
  assert.equal(projected.sender_phone, "+41798559737");
  assert.equal((projected.raw_payload as any).nexus_self_test_original_chat_jid, "174488096354391@lid");
});
