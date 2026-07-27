import type {
  FromMeInboundMode,
  NormalizedInboundMessage,
  WhatsAppMediaKind
} from "./types.js";

export interface InboundMapperOptions {
  allowFromMeInbound?: boolean;
  fromMeMode?: FromMeInboundMode;
  fromMeTestPrefix?: string;
}

interface ExtractedContent {
  body: string;
  mediaId: string | null;
  mediaKind: WhatsAppMediaKind | null;
  mediaMimeType: string | null;
  mediaFilename: string | null;
  replyToMessageId: string | null;
}

function phoneFromJid(jid: string | undefined | null): string | null {
  if ((jid || "").endsWith("@lid")) return null;
  const cleaned = (jid || "").split("@")[0]?.split(":")[0]?.replace(/\D/g, "") || "";
  return cleaned ? `+${cleaned}` : null;
}

function extractText(message: any): ExtractedContent {
  const content = message?.message || {};
  const extended = content.extendedTextMessage || {};
  const context =
    extended.contextInfo ||
    content.imageMessage?.contextInfo ||
    content.videoMessage?.contextInfo ||
    content.audioMessage?.contextInfo ||
    content.documentMessage?.contextInfo ||
    {};
  if (content.conversation) {
    return {
      body: String(content.conversation).trim(),
      mediaId: null,
      mediaKind: null,
      mediaMimeType: null,
      mediaFilename: null,
      replyToMessageId: context.stanzaId ? String(context.stanzaId) : null
    };
  }
  if (extended.text) {
    return {
      body: String(extended.text).trim(),
      mediaId: null,
      mediaKind: null,
      mediaMimeType: null,
      mediaFilename: null,
      replyToMessageId: context.stanzaId ? String(context.stanzaId) : null
    };
  }
  for (const [kind, value] of [
    ["image", content.imageMessage],
    ["video", content.videoMessage],
    ["audio", content.audioMessage],
    ["document", content.documentMessage],
    ["sticker", content.stickerMessage]
  ] as const) {
    if (!value) continue;
    const filename = String(value.fileName || "").trim().slice(0, 255) || null;
    const caption = String(value.caption || filename || "").trim();
    return {
      body: `<media:${kind}>${caption ? ` ${caption}` : ""}`,
      mediaId: value.url ? String(value.url) : value.directPath ? String(value.directPath) : null,
      mediaKind: kind,
      mediaMimeType: value.mimetype ? String(value.mimetype).split(";", 1)[0].trim().toLowerCase() : null,
      mediaFilename: filename,
      replyToMessageId: value.contextInfo?.stanzaId
        ? String(value.contextInfo.stanzaId)
        : null
    };
  }
  if (content.locationMessage) {
    const location = content.locationMessage;
    const coordinates = `${location.degreesLatitude ?? "unknown"},${location.degreesLongitude ?? "unknown"}`;
    const label = String(location.name || location.address || "").trim();
    return {
      body: `<location:${coordinates}>${label ? ` ${label}` : ""}`,
      mediaId: null,
      mediaKind: null,
      mediaMimeType: null,
      mediaFilename: null,
      replyToMessageId: null
    };
  }
  if (content.contactMessage || content.contactsArrayMessage) {
    return {
      body: "<contacts>",
      mediaId: null,
      mediaKind: null,
      mediaMimeType: null,
      mediaFilename: null,
      replyToMessageId: null
    };
  }
  return {
    body: "",
    mediaId: null,
    mediaKind: null,
    mediaMimeType: null,
    mediaFilename: null,
    replyToMessageId: null
  };
}

function messageType(message: any): string {
  const content = message?.message || {};
  return Object.keys(content)[0] || "unknown";
}

function isCustomerChatJid(jid: string): boolean {
  if (!jid || jid === "status@broadcast") return false;
  return !(
    jid.endsWith("@broadcast") ||
    jid.endsWith("@g.us") ||
    jid.endsWith("@newsletter")
  );
}

function boundedRawMessage(event: any): Record<string, unknown> {
  const key = event?.key || {};
  return {
    key: {
      id: key.id ? String(key.id) : null,
      remoteJid: key.remoteJid ? String(key.remoteJid) : null,
      participant: key.participant ? String(key.participant) : null,
      fromMe: key.fromMe === true
    },
    messageTimestamp: event?.messageTimestamp ? String(event.messageTimestamp) : null,
    pushName: event?.pushName ? String(event.pushName).slice(0, 160) : null,
    message_type: messageType(event)
  };
}

export function normalizeBaileysInbound(
  accountId: string,
  event: any,
  options: InboundMapperOptions = {}
): NormalizedInboundMessage | null {
  if (!event?.key?.id) return null;
  const fromMe = event.key.fromMe === true;
  const fromMeMode = options.fromMeMode || "ignore";
  const testPrefix = options.fromMeTestPrefix || "NEXUS_SELF_INBOUND_TEST";
  if (fromMe && (!options.allowFromMeInbound || fromMeMode === "ignore")) return null;
  const chatJid = String(event.key.remoteJid || "");
  const senderJid = String(event.key.participant || event.key.remoteJid || "");
  if (!isCustomerChatJid(chatJid)) return null;
  const extracted = extractText(event);
  if (!extracted.body) return null;
  let projectionMode: NormalizedInboundMessage["projection_mode"] = "visitor";
  if (fromMe) {
    if (fromMeMode === "store_only") {
      projectionMode = "store_only";
    } else if (fromMeMode === "test_visitor") {
      if (!extracted.body.startsWith(testPrefix)) return null;
      projectionMode = "test_visitor";
    } else {
      return null;
    }
  }
  const timestamp = Number(event.messageTimestamp || Date.now() / 1000);
  const normalized: NormalizedInboundMessage = {
    transport: "baileys_sidecar",
    account_id: accountId,
    external_message_id: String(event.key.id),
    chat_jid: chatJid,
    sender_jid: senderJid,
    sender_phone: phoneFromJid(senderJid),
    sender_name: event.pushName ? String(event.pushName).slice(0, 160) : null,
    message_type: messageType(event),
    body_text: extracted.body,
    raw_message: boundedRawMessage(event),
    received_at: new Date(timestamp * 1000).toISOString(),
    from_me: fromMe,
    projection_mode: projectionMode,
    reply_to_message_id: extracted.replyToMessageId,
    media_id: extracted.mediaId,
    media_kind: extracted.mediaKind,
    media_mime_type: extracted.mediaMimeType,
    media_filename: extracted.mediaFilename
  };
  if (fromMe && projectionMode === "test_visitor") {
    normalized.self_echo_test_prefix = testPrefix;
  }
  return normalized;
}
