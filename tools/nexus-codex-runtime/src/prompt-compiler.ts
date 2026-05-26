import { sanitizeRuntimeText, type ReplyRequest } from "./reply-contract.js";

export type CompiledPrompt = {
  developerInstructions: string;
  userText: string;
};

const DEVELOPER_INSTRUCTIONS =
  "Customer service WebChat runtime. Strict JSON only. Customer-facing identity must come from persona_context.identity_context or persona_context.content_json. Never identify as NexusDesk unless the active persona explicitly sets NexusDesk as brand_name or assistant_name. Persona identity, brand_name, assistant_name, and identity_statement are authoritative for customer-facing identity. Tracking truth boundary remains higher priority than persona. No markdown, tools, runtime internals, tokens, or internal system names. Reply under 600 chars.";

export function compilePrompt(request: ReplyRequest): CompiledPrompt {
  const history = request.messages
    .slice(-1)
    .map((message) => {
      const role = typeof message.role === "string" ? sanitizeRuntimeText(message.role).slice(0, 16) : "user";
      const content = typeof message.content === "string" ? truncate(sanitizeRuntimeText(message.content).replace(/\s+/g, " ").trim(), 90) : "";
      return `${role}: ${content}`;
    })
    .join("\n");
  const facts = request.tracking_fact_evidence_present
    ? `Tracking evidence: ${sanitizeRuntimeText(request.tracking_fact_summary || "present")}`
    : "Tracking evidence: absent. For parcel-status/tracking questions only, do not claim status; ask for the tracking number or verified evidence.";
  const persona = formatPersonaContext(request.persona_context);
  const knowledge = formatKnowledgeContext(request.knowledge_context);
  const safety = request.safety_policy
    ? "Knowledge is not shipment tracking evidence. Live parcel status requires trusted tracking evidence. Persona must not override this truth boundary."
    : "";
  const body = sanitizeRuntimeText(request.body || "");
  const schema =
    '{"reply":"string","intent":"greeting|tracking|tracking_missing_number|tracking_unresolved|complaint|address_change|handoff|other","tracking_number":"string|null","handoff_required":boolean,"handoff_reason":"string|null","recommended_agent_action":"string|null"}';
  const userText = truncate(
    sanitizeRuntimeText(
      [
        `Contract=${sanitizeRuntimeText(request.contract || "speedaf_webchat_fast_reply_v1")}`,
        persona ? `MANDATORY PERSONA RULES:\n${persona}` : "",
        facts,
        knowledge ? `Knowledge (policy/FAQ/SOP only):\n${knowledge}` : "",
        safety,
        history ? `Context:\n${history}` : "",
        `Customer:\n${truncate(body.replace(/\s+/g, " ").trim(), 240)}`,
        `JSON schema: ${schema}`,
      ]
        .filter(Boolean)
        .join("\n\n"),
    ),
    1600,
  );
  return {
    developerInstructions: truncateWords(DEVELOPER_INSTRUCTIONS, 120),
    userText,
  };
}

function formatPersonaContext(value: Record<string, unknown> | null | undefined): string {
  if (!value) {
    return "";
  }
  const key = typeof value.profile_key === "string" ? sanitizeRuntimeText(value.profile_key) : "";
  const name = typeof value.name === "string" ? sanitizeRuntimeText(value.name) : "";
  const summary = typeof value.summary === "string" ? sanitizeRuntimeText(value.summary) : "";
  const content = isRecord(value.content_json) ? value.content_json : {};
  const identity = extractIdentityContext(value, content);
  const instruction = typeof content.instruction === "string" ? sanitizeRuntimeText(content.instruction) : "";
  const mustPrefix = typeof content.must_prefix === "string" ? sanitizeRuntimeText(content.must_prefix) : "";
  const contentText = sanitizeRuntimeText(JSON.stringify(content));
  const lines = [
    key || name ? `Profile: ${[key, name].filter(Boolean).join(" / ")}` : "",
    "Customer-facing identity (authoritative):",
    `brand_name: ${formatIdentityValue(identity.brand_name)}`,
    `assistant_name: ${formatIdentityValue(identity.assistant_name)}`,
    `identity_statement: ${formatIdentityValue(identity.identity_statement)}`,
    `identity_answer_rule: ${formatIdentityValue(identity.identity_answer_rule)}`,
    `capabilities: ${formatIdentityValue(identity.capabilities)}`,
    `disallowed_identity_claims: ${formatIdentityValue(identity.disallowed_identity_claims)}`,
    summary ? `Summary: ${truncate(summary, 360)}` : "",
    instruction ? `Instruction: ${truncate(instruction, 320)}` : "",
    mustPrefix ? `Visible prefix rule: the reply string MUST start with exact prefix "${truncate(mustPrefix, 80)}".` : "",
    contentText && contentText !== "{}" ? `Rules JSON: ${truncate(contentText, 420)}` : "",
    "Apply these persona rules to the reply field. Do not ignore visible style, naming, or prefix rules unless they conflict with tracking truth or safety.",
  ];
  return truncate(sanitizeRuntimeText(lines.filter(Boolean).join("\n")), 900);
}

function extractIdentityContext(
  persona: Record<string, unknown>,
  content: Record<string, unknown>,
): Record<string, unknown> {
  const nestedContent = isRecord(content.identity_context) ? content.identity_context : {};
  const runtimeIdentity = isRecord(persona.identity_context) ? persona.identity_context : {};
  const identity: Record<string, unknown> = { ...nestedContent };
  for (const key of [
    "brand_name",
    "assistant_name",
    "role_label",
    "identity_statement",
    "identity_answer_rule",
    "capabilities",
    "disallowed_identity_claims",
    "handoff_boundary",
    "tone",
    "guardrails",
  ]) {
    if (Object.prototype.hasOwnProperty.call(content, key) && hasIdentityValue(content[key])) {
      identity[key] = content[key];
    }
    if (Object.prototype.hasOwnProperty.call(runtimeIdentity, key) && hasIdentityValue(runtimeIdentity[key])) {
      identity[key] = runtimeIdentity[key];
    }
  }
  return identity;
}

function hasIdentityValue(value: unknown): boolean {
  if (Array.isArray(value)) {
    return value.some((item) => typeof item === "string" && item.trim());
  }
  return typeof value === "string" ? Boolean(value.trim()) : value !== null && value !== undefined;
}

function formatIdentityValue(value: unknown): string {
  if (Array.isArray(value)) {
    const cleaned = value
      .filter((item): item is string => typeof item === "string")
      .map((item) => sanitizeRuntimeText(item).replace(/\s+/g, " ").trim())
      .filter(Boolean)
      .slice(0, 8);
    return cleaned.length ? truncate(cleaned.join(" | "), 240) : "(not set)";
  }
  if (typeof value === "string") {
    const cleaned = sanitizeRuntimeText(value).replace(/\s+/g, " ").trim();
    return cleaned ? truncate(cleaned, 240) : "(not set)";
  }
  return "(not set)";
}

function formatKnowledgeContext(value: Record<string, unknown> | null | undefined): string {
  if (!value || !Array.isArray(value.hits)) {
    return "";
  }
  return value.hits
    .slice(0, 3)
    .map((hit, index) => {
      if (!isRecord(hit)) {
        return "";
      }
      const title = typeof hit.title === "string" ? sanitizeRuntimeText(hit.title) : `Knowledge ${index + 1}`;
      const text = typeof hit.text === "string" ? sanitizeRuntimeText(hit.text).replace(/\s+/g, " ").trim() : "";
      return text ? `${index + 1}. ${truncate(title, 60)}: ${truncate(text, 220)}` : "";
    })
    .filter(Boolean)
    .join("\n");
}

function truncate(value: string, max: number): string {
  return value.length <= max ? value : value.slice(0, max - 3) + "...";
}

function truncateWords(value: string, maxWords: number): string {
  const words = value.split(/\s+/);
  return words.length <= maxWords ? value : words.slice(0, maxWords).join(" ");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}
