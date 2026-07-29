import assert from "node:assert/strict";
import test from "node:test";
import { normalizeBaileysInbound } from "./inboundMapper.js";

test("normalizes direct text without retaining the complete provider event", () => {
  const normalized = normalizeBaileysInbound("wa-main", {
    key: { id: "msg-1", remoteJid: "15551234567@s.whatsapp.net", fromMe: false },
    message: { conversation: "hello" },
    messageTimestamp: 1781179200,
    pushName: "Customer",
    unexpectedSensitiveEnvelope: { should_not_persist: true }
  });
  assert.equal(normalized?.transport, "baileys_sidecar");
  assert.equal(normalized?.external_message_id, "msg-1");
  assert.equal(normalized?.sender_phone, "+15551234567");
  assert.equal(normalized?.sender_name, "Customer");
  assert.equal(normalized?.body_text, "hello");
  assert.equal(normalized?.message_type, "conversation");
  assert.equal(normalized?.from_me, false);
  assert.equal(normalized?.projection_mode, "visitor");
  assert.equal("unexpectedSensitiveEnvelope" in (normalized?.raw_message as object), false);
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

test("normalizes media-only messages to a durable placeholder", () => {
  const normalized = normalizeBaileysInbound("wa-main", {
    key: { id: "media-1", remoteJid: "15551234567@s.whatsapp.net", fromMe: false },
    message: {
      imageMessage: {
        mimetype: "image/jpeg",
        directPath: "/v/t62/example",
        caption: "damaged parcel",
        contextInfo: { stanzaId: "quoted-1" }
      }
    }
  });
  assert.equal(normalized?.body_text, "<media:image> damaged parcel");
  assert.equal(normalized?.media_mime_type, "image/jpeg");
  assert.equal(normalized?.reply_to_message_id, "quoted-1");
});

test("unwraps ephemeral and view-once provider containers", () => {
  const text = normalizeBaileysInbound("wa-main", {
    key: { id: "wrapped-text", remoteJid: "15551234567@s.whatsapp.net", fromMe: false },
    message: {
      ephemeralMessage: {
        message: {
          extendedTextMessage: {
            text: "wrapped hello",
            contextInfo: { stanzaId: "quoted-wrapped" }
          }
        }
      }
    }
  });
  assert.equal(text?.body_text, "wrapped hello");
  assert.equal(text?.message_type, "extendedTextMessage");
  assert.equal(text?.reply_to_message_id, "quoted-wrapped");

  const media = normalizeBaileysInbound("wa-main", {
    key: { id: "wrapped-media", remoteJid: "15551234567@s.whatsapp.net", fromMe: false },
    message: {
      viewOnceMessageV2: {
        message: {
          imageMessage: {
            mimetype: "image/jpeg",
            caption: "wrapped image"
          }
        }
      }
    }
  });
  assert.equal(media?.body_text, "<media:image> wrapped image");
  assert.equal(media?.message_type, "imageMessage");
  assert.equal(media?.media_kind, "image");
  assert.equal(media?.media_mime_type, "image/jpeg");
});

test("normalizes interactive button list template and native-flow responses", () => {
  const cases = [
    {
      id: "button-response",
      message: {
        buttonsResponseMessage: {
          selectedDisplayText: "Track parcel",
          selectedButtonId: "track-parcel",
          contextInfo: { stanzaId: "button-origin" }
        }
      },
      body: "Track parcel",
      type: "buttonsResponseMessage",
      reply: "button-origin"
    },
    {
      id: "list-response",
      message: {
        listResponseMessage: {
          title: "Damaged parcel",
          singleSelectReply: { selectedRowId: "damage-claim" },
          contextInfo: { stanzaId: "list-origin" }
        }
      },
      body: "Damaged parcel",
      type: "listResponseMessage",
      reply: "list-origin"
    },
    {
      id: "template-response",
      message: {
        templateButtonReplyMessage: {
          selectedDisplayText: "Talk to agent",
          selectedId: "human-agent",
          contextInfo: { stanzaId: "template-origin" }
        }
      },
      body: "Talk to agent",
      type: "templateButtonReplyMessage",
      reply: "template-origin"
    },
    {
      id: "native-flow-response",
      message: {
        interactiveResponseMessage: {
          nativeFlowResponseMessage: {
            paramsJson: JSON.stringify({
              id: "delivery-delay",
              title: "Delivery delayed",
              flow_token: "must-not-project"
            })
          },
          contextInfo: { stanzaId: "native-origin" }
        }
      },
      body: "Delivery delayed",
      type: "interactiveResponseMessage",
      reply: "native-origin"
    }
  ] as const;

  for (const item of cases) {
    const normalized = normalizeBaileysInbound("wa-main", {
      key: {
        id: item.id,
        remoteJid: "15551234567@s.whatsapp.net",
        fromMe: false
      },
      message: item.message,
      messageTimestamp: 1781179200
    });
    assert.equal(normalized?.body_text, item.body);
    assert.equal(normalized?.message_type, item.type);
    assert.equal(normalized?.reply_to_message_id, item.reply);
    assert.equal(JSON.stringify(normalized?.raw_message).includes("must-not-project"), false);
  }
});

test("ignores outbound, group, broadcast, newsletter, and empty messages", () => {
  assert.equal(normalizeBaileysInbound("wa-main", { key: { id: "x", remoteJid: "15551234567@s.whatsapp.net", fromMe: true }, message: { conversation: "self" } }), null);
  assert.equal(normalizeBaileysInbound("wa-main", { key: { id: "x", remoteJid: "1@g.us" }, message: { conversation: "hi" } }), null);
  assert.equal(normalizeBaileysInbound("wa-main", { key: { id: "x", remoteJid: "status@broadcast" }, message: { conversation: "status update" } }), null);
  assert.equal(normalizeBaileysInbound("wa-main", { key: { id: "x", remoteJid: "12345@broadcast" }, message: { conversation: "broadcast" } }), null);
  assert.equal(normalizeBaileysInbound("wa-main", { key: { id: "x", remoteJid: "12345@newsletter" }, message: { conversation: "newsletter" } }), null);
  assert.equal(normalizeBaileysInbound("wa-main", { key: { id: "x", remoteJid: "15551234567@s.whatsapp.net" }, message: {} }), null);
});

test("normalizes fromMe as store_only only when explicitly allowed", () => {
  const normalized = normalizeBaileysInbound("wa-main", {
    key: { id: "self-store", remoteJid: "15551234567@s.whatsapp.net", fromMe: true },
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
    key: { id: "self-no-prefix", remoteJid: "15551234567@s.whatsapp.net", fromMe: true },
    message: { conversation: "hello" }
  }, {
    allowFromMeInbound: true,
    fromMeMode: "test_visitor",
    fromMeTestPrefix: "SELF_TEST"
  });
  assert.equal(withoutPrefix, null);

  const withPrefix = normalizeBaileysInbound("wa-main", {
    key: { id: "self-prefix", remoteJid: "15551234567@s.whatsapp.net", fromMe: true },
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
