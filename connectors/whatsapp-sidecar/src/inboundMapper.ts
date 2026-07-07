import type { FromMeInboundMode, NormalizedInboundMessage } from "./types.js";

export interface InboundMapperOptions {
  allowFromMeInbound?: boolean;
  fromMeMode?: FromMeInboundMode;
  fromMeTestPrefix?: string;
}

function phoneFromJid(jid: string | undefined | null): string | null {
  if ((jid || "").endsWith("@lid")) return null;
  const cleaned = (jid || "").split("@")[0]?.replace(/\D/g, "") || "";
  return cleaned ? `+${cleaned}` : null;
}

function extractText(message: any): string {
  const content = message?.message || {};
  return (
    content.conversation ||
    content.extendedTextMessage?.text ||
    content.imageMessage?.caption ||
    content.videoMessage?.caption ||
    ""
  ).trim();
}

function messageType(message: any): string {
  const content = message?.message || {};
  const first = Object.keys(content)[0];
  return first || "unknown";
}

function isCustomerChatJid(jid: string): boolean {
  if (!jid) return false;
  if (jid === "status@broadcast") return false;
  if (jid.endsWith("@broadcast")) return false;
  if (jid.endsWith("@g.us")) return false;
  if (jid.endsWith("@newsletter")) return false;
  return true;
}

export function normalizeBaileysInbound(accountId: string, event: any, options: InboundMapperOptions = {}): NormalizedInboundMessage | null {
  if (!event?.key?.id) return null;
  const fromMe = event.key.fromMe === true;
  const fromMeMode = options.fromMeMode || "ignore";
  const testPrefix = options.fromMeTestPrefix || "NEXUS_SELF_INBOUND_TEST";
  if (fromMe && (!options.allowFromMeInbound || fromMeMode === "ignore")) return null;
  const chatJid = String(event.key.remoteJid || "");
  const senderJid = String(event.key.participant || event.key.remoteJid || "");
  if (!isCustomerChatJid(chatJid)) return null;
  const body = extractText(event);
  if (!body) return null;
  let projectionMode: NormalizedInboundMessage["projection_mode"] = "visitor";
  if (fromMe) {
    if (fromMeMode === "store_only") {
      projectionMode = "store_only";
    } else if (fromMeMode === "test_visitor") {
      if (!body.startsWith(testPrefix)) return null;
      projectionMode = "test_visitor";
    } else if (fromMeMode === "self_chat") {
      return null;
    } else {
      return null;
    }
  }
  const timestamp = Number(event.messageTimestamp || Date.now() / 1000);
  const normalized: NormalizedInboundMessage = {
    account_id: accountId,
    external_message_id: String(event.key.id),
    chat_jid: chatJid,
    sender_jid: senderJid,
    sender_phone: phoneFromJid(senderJid),
    message_type: messageType(event),
    body_text: body,
    raw_payload: event,
    received_at: new Date(timestamp * 1000).toISOString(),
    from_me: fromMe,
    projection_mode: projectionMode
  };
  if (fromMe && projectionMode === "test_visitor") {
    normalized.self_echo_test_prefix = testPrefix;
  }
  return normalized;
}
