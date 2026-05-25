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
    body: typeof value.body === "string" ? value.body : "",
    messages: Array.isArray(value.messages) ? value.messages.filter(isRecord) : [],
    contract: typeof value.contract === "string" ? value.contract : undefined,
    tracking_fact_summary:
      typeof value.tracking_fact_summary === "string" ? value.tracking_fact_summary : null,
    tracking_fact_evidence_present: value.tracking_fact_evidence_present === true,
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
