import { RuntimeError } from "./errors.js";

export const INTENTS = [
  "greeting",
  "tracking",
  "tracking_missing_number",
  "tracking_unresolved",
  "complaint",
  "address_change",
  "handoff",
  "other",
] as const;

export type ReplyIntent = (typeof INTENTS)[number];

export type LoginPayload = {
  type: "chatgptAuthTokens";
  accessToken: string;
  chatgptAccountId: string;
  chatgptPlanType?: string | null;
};

export type ReplyRequest = {
  login: LoginPayload;
  body: string;
  messages: Array<{ role?: string; content?: string }>;
  contract?: string;
  tracking_fact_summary?: string | null;
  tracking_fact_evidence_present?: boolean;
  persona_context?: Record<string, unknown> | null;
  knowledge_context?: Record<string, unknown> | null;
  safety_policy?: Record<string, unknown> | null;
  tenant_id?: string;
  channel_key?: string;
  session_id?: string;
};

export type StrictReply = {
  reply: string;
  intent: ReplyIntent;
  tracking_number: string | null;
  handoff_required: boolean;
  handoff_reason: string | null;
  recommended_agent_action: string | null;
};

export function validateReplyRequest(value: unknown): ReplyRequest {
  if (!isRecord(value)) {
    throw new RuntimeError(400, "codex_request_invalid", "request_body_must_be_object");
  }
  const login = value.login;
  if (!isRecord(login)) {
    throw new RuntimeError(401, "codex_auth_missing", "missing_login");
  }
  if (login.type !== "chatgptAuthTokens") {
    throw new RuntimeError(401, "codex_auth_invalid", "unsupported_login_type");
  }
  if (typeof login.accessToken !== "string" || !login.accessToken.trim()) {
    throw new RuntimeError(401, "codex_auth_missing", "missing_access_token");
  }
  if (typeof login.chatgptAccountId !== "string" || !login.chatgptAccountId.trim()) {
    throw new RuntimeError(401, "codex_auth_missing", "missing_chatgpt_account_id");
  }
  return {
    login: {
      type: "chatgptAuthTokens",
      accessToken: login.accessToken,
      chatgptAccountId: login.chatgptAccountId,
      chatgptPlanType: typeof login.chatgptPlanType === "string" ? login.chatgptPlanType : null,
    },
    body: typeof value.body === "string" ? sanitizeRuntimeText(value.body) : "",
    messages: Array.isArray(value.messages)
      ? value.messages.filter(isRecord).map((message) => ({
          role: typeof message.role === "string" ? sanitizeRuntimeText(message.role) : undefined,
          content: typeof message.content === "string" ? sanitizeRuntimeText(message.content) : undefined,
        }))
      : [],
    contract: typeof value.contract === "string" ? sanitizeRuntimeText(value.contract) : undefined,
    tracking_fact_summary:
      typeof value.tracking_fact_summary === "string" ? sanitizeRuntimeText(value.tracking_fact_summary) : null,
    tracking_fact_evidence_present: value.tracking_fact_evidence_present === true,
    persona_context: isRecord(value.persona_context) ? sanitizeRecord(value.persona_context, 2600) : null,
    knowledge_context: isRecord(value.knowledge_context) ? sanitizeRecord(value.knowledge_context, 5000) : null,
    safety_policy: isRecord(value.safety_policy) ? sanitizeRecord(value.safety_policy, 2000) : null,
    tenant_id: typeof value.tenant_id === "string" && value.tenant_id.trim() ? value.tenant_id.trim() : "default",
    channel_key: typeof value.channel_key === "string" && value.channel_key.trim() ? value.channel_key.trim() : "website",
    session_id: typeof value.session_id === "string" ? value.session_id : undefined,
  };
}

export function parseStrictReply(text: string): StrictReply {
  let decoded: unknown;
  try {
    decoded = JSON.parse(text.trim());
  } catch {
    throw new RuntimeError(502, "codex_invalid_output", "assistant_output_not_json", "parse");
  }
  if (!isRecord(decoded)) {
    throw new RuntimeError(502, "codex_invalid_output", "assistant_output_not_object", "parse");
  }
  const reply = decoded.reply;
  const intent = decoded.intent;
  const trackingNumber = decoded.tracking_number;
  const handoffRequired = decoded.handoff_required;
  const handoffReason = decoded.handoff_reason;
  const recommended = decoded.recommended_agent_action;
  if (typeof reply !== "string" || !reply.trim() || reply.length > 600) {
    throw new RuntimeError(502, "codex_invalid_output", "reply_invalid", "parse");
  }
  if (typeof intent !== "string" || !INTENTS.includes(intent as ReplyIntent)) {
    throw new RuntimeError(502, "codex_invalid_output", "intent_invalid", "parse");
  }
  if (trackingNumber !== null && typeof trackingNumber !== "string") {
    throw new RuntimeError(502, "codex_invalid_output", "tracking_number_invalid", "parse");
  }
  if (typeof handoffRequired !== "boolean") {
    throw new RuntimeError(502, "codex_invalid_output", "handoff_required_invalid", "parse");
  }
  if (handoffReason !== null && typeof handoffReason !== "string") {
    throw new RuntimeError(502, "codex_invalid_output", "handoff_reason_invalid", "parse");
  }
  if (recommended !== null && typeof recommended !== "string") {
    throw new RuntimeError(502, "codex_invalid_output", "recommended_agent_action_invalid", "parse");
  }
  return {
    reply: reply.trim(),
    intent: intent as ReplyIntent,
    tracking_number: trackingNumber,
    handoff_required: handoffRequired,
    handoff_reason: handoffReason,
    recommended_agent_action: recommended,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

export function sanitizeRuntimeText(value: string): string {
  return value
    .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]{8,}\b/gi, "[REDACTED_SECRET]")
    .replace(/\b(sk|pk|rk)-[A-Za-z0-9_-]{12,}\b/g, "[REDACTED_SECRET]")
    .replace(
      /(["']?\b(?:access[_-]?token|api[_-]?key|secret|password|authorization)\b["']?\s*[:=]\s*["']?)[^"'\s,;)\]}]{4,}/gi,
      "$1[REDACTED_SECRET]",
    )
    .replace(
      /\bhttps?:\/\/(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}|[^/\s"')]+(?:\.internal|\.local|\.lan|\.svc|\.cluster\.local))(?:[^\s"')<>{}]*)?/gi,
      "[REDACTED_INTERNAL_URL]",
    )
    .replace(/provider[_-]?runtime/gi, "[REDACTED_INTERNAL_TERM]")
    .replace(/codex[_-]?app[_-]?server/gi, "[REDACTED_INTERNAL_TERM]")
    .replace(/\bcodex\s+app\s+server\b/gi, "[REDACTED_INTERNAL_TERM]")
    .replace(/\bsystem\s+prompt\b/gi, "[REDACTED_INTERNAL_TERM]")
    .replace(/\bopenclaw\b/gi, "[REDACTED_INTERNAL_TERM]")
    .replace(/\bbridge\b/gi, "[REDACTED_INTERNAL_TERM]");
}

function sanitizeRecord(value: Record<string, unknown>, maxJsonChars: number): Record<string, unknown> {
  const sanitized = sanitizeRuntimeValue(value);
  const json = JSON.stringify(sanitized);
  if (json.length <= maxJsonChars) {
    return sanitized as Record<string, unknown>;
  }
  return { truncated: true, preview: sanitizeRuntimeText(json.slice(0, maxJsonChars - 16)) };
}

function sanitizeRuntimeValue(value: unknown, depth = 0): unknown {
  if (typeof value === "string") {
    return sanitizeRuntimeText(value);
  }
  if (Array.isArray(value)) {
    if (depth >= 6) {
      return "[REDACTED_NESTED_CONTEXT]";
    }
    return value.slice(0, 50).map((item) => sanitizeRuntimeValue(item, depth + 1));
  }
  if (isRecord(value)) {
    if (depth >= 6) {
      return { redacted: true };
    }
    const output: Record<string, unknown> = {};
    for (const [key, entry] of Object.entries(value).slice(0, 80)) {
      output[sanitizeRuntimeText(key).slice(0, 120)] = sanitizeRuntimeValue(entry, depth + 1);
    }
    return output;
  }
  return value;
}
