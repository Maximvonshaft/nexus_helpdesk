import { normalizeMessageContent } from "@whiskeysockets/baileys";
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

function normalizedContent(message: any): Record<string, any> {
  const normalized = normalizeMessageContent(message?.message);
  return normalized && typeof normalized === "object" ? (normalized as Record<string, any>) : {};
}

function boundedText(value: unknown): string {
  return String(value ?? "").trim().slice(0, 4096);
}

function firstText(...values: unknown[]): string {
  for (const value of values) {
    const normalized = boundedText(value);
    if (normalized) return normalized;
  }
  return "";
}

function nativeFlowSelection(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) return "";
  try {
    const parsed = JSON.parse(value) as Record<string, unknown>;
    return firstText(
      parsed.title,
      parsed.text,
      parsed.display_text,
      parsed.selected_display_text,
      parsed.id,
      parsed.row_id,
      parsed.selected_id
    );
  } catch {
    return "";
  }
}

function interactiveResponse(content: Record<string, any>): ExtractedContent | null {
  const buttons = content.buttonsResponseMessage;
  if (buttons) {
    const body = firstText(buttons.selectedDisplayText, buttons.selectedButtonId);
    if (body) {
      return {
        body,
        mediaKind: null,
        mediaMimeType: null,
        mediaFilename: null,
        replyToMessageId: buttons.contextInfo?.stanzaId
          ? String(buttons.contextInfo.stanzaId)
          : null
      };
    }
  }

  const list = content.listResponseMessage;
  if (list) {
    const body = firstText(
      list.title,
      list.description,
      list.singleSelectReply?.selectedRowId
    );
    if (body) {
      return {
        body,
        mediaKind: null,
        mediaMimeType: null,
        mediaFilename: null,
        replyToMessageId: list.contextInfo?.stanzaId
          ? String(list.contextInfo.stanzaId)
          : null
      };
    }
  }

  const template = content.templateButtonReplyMessage;
  if (template) {
    const body = firstText(template.selectedDisplayText, template.selectedId);
    if (body) {
      return {
        body,
        mediaKind: null,
        mediaMimeType: null,
        mediaFilename: null,
        replyToMessageId: template.contextInfo?.stanzaId
          ? String(template.contextInfo.stanzaId)
          : null
      };
    }
  }

  const interactive = content.interactiveResponseMessage;
  if (interactive) {
    const body = firstText(
      nativeFlowSelection(interactive.nativeFlowResponseMessage?.paramsJson),
      interactive.body?.text
    );
    if (body) {
      return {
        body,
        mediaKind: null,
        mediaMimeType: null,
        mediaFilename: null,
        replyToMessageId: interactive.contextInfo?.stanzaId
          ? String(interactive.contextInfo.stanzaId)
          : null
      };
    }
  }

  return null;
}

function extractText(message: any): ExtractedContent {
  const content = normalizedContent(message);
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
      mediaKind: null,
      mediaMimeType: null,
      mediaFilename: null,
      replyToMessageId: context.stanzaId ? String(context.stanzaId) : null
    };
  }
  if (extended.text) {
    return {
      body: String(extended.text).trim(),
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
      mediaKind: null,
      mediaMimeType: null,
      mediaFilename: null,
      replyToMessageId: null
    };
  }
  if (content.contactMessage || content.contactsArrayMessage) {
    return {
      body: "<contacts>",
      mediaKind: null,
      mediaMimeType: null,
      mediaFilename: null,
      replyToMessageId: null
    };
  }
  const response = interactiveResponse(content);
  if (response) return response;
  return {
    body: "",
    mediaKind: null,
    mediaMimeType: null,
    mediaFilename: null,
    replyToMessageId: null
  };
}

function messageType(message: any): string {
  return Object.keys(normalizedContent(message))[0] || "unknown";
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
  const externalMessageId = String(event.key.id);
  const normalized: NormalizedInboundMessage = {
    transport: "baileys_sidecar",
    account_id: accountId,
    external_message_id: externalMessageId,
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
    media_kind: extracted.mediaKind,
    media_mime_type: extracted.mediaMimeType,
    media_filename: extracted.mediaFilename,
    reply_to_message_id: extracted.replyToMessageId
  };
  return normalized;
}
