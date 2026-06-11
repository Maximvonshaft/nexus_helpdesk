import type { NormalizedInboundMessage } from "./types.js";

function phoneFromJid(jid: string | undefined | null): string | null {
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

export function normalizeBaileysInbound(accountId: string, event: any): NormalizedInboundMessage | null {
  if (!event?.key?.id || event.key.fromMe) return null;
  const chatJid = String(event.key.remoteJid || "");
  const senderJid = String(event.key.participant || event.key.remoteJid || "");
  if (!chatJid || chatJid.endsWith("@g.us")) return null;
  const body = extractText(event);
  if (!body) return null;
  const timestamp = Number(event.messageTimestamp || Date.now() / 1000);
  return {
    account_id: accountId,
    external_message_id: String(event.key.id),
    chat_jid: chatJid,
    sender_jid: senderJid,
    sender_phone: phoneFromJid(senderJid),
    message_type: messageType(event),
    body_text: body,
    raw_payload: event,
    received_at: new Date(timestamp * 1000).toISOString()
  };
}
