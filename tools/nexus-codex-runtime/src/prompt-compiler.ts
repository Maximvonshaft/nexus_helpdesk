import { sanitizeRuntimeText, type ReplyRequest } from "./reply-contract.js";

export type CompiledPrompt = {
  developerInstructions: string;
  userText: string;
};

const DEVELOPER_INSTRUCTIONS =
  "Customer service WebChat runtime. Strict JSON only. Customer-facing identity must come from persona_context.identity_context or persona_context.content_json. Never identify as NexusDesk unless the active persona explicitly sets NexusDesk as brand_name or assistant_name. Persona identity, brand_name, assistant_name, and identity_statement are authoritative for customer-facing identity. Tracking truth boundary remains higher priority than persona. No markdown, tools, runtime internals, tokens, or internal system names. Reply under 600 chars.";

const MAX_USER_PROMPT_CHARS = 2400;
const MAX_KNOWLEDGE_CONTEXT_CHARS = 1400;
const MAX_KNOWLEDGE_HITS = 4;
const DIRECT_ANSWER_SCORE_THRESHOLD = 24;
const MAX_DIRECT_ANSWER_CHARS = 700;
const MAX_DIRECT_TEXT_CHARS = 520;
const MAX_NORMAL_TEXT_CHARS = 220;

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
    MAX_USER_PROMPT_CHARS,
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
  const hits = value.hits.filter(isRecord);
  if (!hits.length) {
    return "";
  }
  const selected = selectKnowledgeHits(hits);
  const lines = [
    "Knowledge facts below (policy/FAQ/SOP only; not live parcel tracking evidence):",
    "Use the knowledge facts below when directly relevant. If a top knowledge item directly answers the customer question, answer from it. Do not say cannot confirm when the answer is present in knowledge. Do not use knowledge documents as live parcel tracking evidence.",
  ];
  selected.forEach((hit, index) => {
    const metadata = isRecord(hit.metadata) ? hit.metadata : {};
    const source = isRecord(hit.source_metadata) ? hit.source_metadata : {};
    const sourceMetadata = {
      source_type: textOrEmpty(metadata.source_type || source.source_type),
      file_name: textOrEmpty(metadata.file_name || source.file_name),
      market_id: metadata.market_id || source.market_id || null,
      channel: textOrEmpty(metadata.channel || source.channel),
      audience_scope: textOrEmpty(metadata.audience_scope || source.audience_scope),
      language: textOrEmpty(metadata.language || source.language),
      citation: textOrEmpty(metadata.citation || source.citation),
    };
    const directAnswer = normalizedText(hit.direct_answer);
    const textLimit = isDirectAnswerHit(hit) ? MAX_DIRECT_TEXT_CHARS : MAX_NORMAL_TEXT_CHARS;
    const text = normalizedText(hit.text);
    lines.push(
      `[KB ${index + 1}] item_key=${truncate(textOrEmpty(hit.item_key), 90)} title=${truncate(textOrEmpty(hit.title) || `Knowledge ${index + 1}`, 90)} score=${scoreOf(hit).toFixed(3)}`,
      `retrieval_method=${truncate(textOrEmpty(hit.retrieval_method), 180)} chunk_index=${textOrEmpty(hit.chunk_index)} answer_mode=${truncate(answerModeOf(hit), 60)} knowledge_kind=${truncate(knowledgeKindOf(hit), 60)} fact_status=${truncate(factStatusOf(hit), 60)}`,
      `matched_terms=${jsonPreview(arrayOfStrings(hit.matched_terms).slice(0, 12), 240)}`,
      `score_breakdown=${jsonPreview(recordPreview(hit.score_breakdown, 10), 360)}`,
      `source_metadata=${jsonPreview(sourceMetadata, 320)}`,
    );
    if (directAnswer) {
      lines.push(`direct_answer=${truncate(directAnswer, MAX_DIRECT_ANSWER_CHARS)}`);
    }
    if (text) {
      lines.push(`text=${truncate(text, textLimit)}`);
    }
  });
  return truncate(lines.join("\n"), MAX_KNOWLEDGE_CONTEXT_CHARS);
}

function selectKnowledgeHits(hits: Array<Record<string, unknown>>): Array<Record<string, unknown>> {
  const selected: Array<Record<string, unknown>> = [];
  const seen = new Set<string>();
  const add = (hit: Record<string, unknown>) => {
    const key = hitKey(hit);
    if (seen.has(key) || selected.length >= MAX_KNOWLEDGE_HITS) {
      return;
    }
    seen.add(key);
    selected.push(hit);
  };

  const priorityHits = hits
    .filter((hit) => isDirectAnswerHit(hit) || isHighConfidenceFactHit(hit))
    .sort(compareKnowledgeHits);
  for (const hit of priorityHits) {
    add(hit);
    if (selected.length >= 2) {
      break;
    }
  }
  for (const hit of hits.slice(0, 3)) {
    add(hit);
  }

  const bestDirectAnswer = hits.filter(isDirectAnswerHit).sort(compareKnowledgeHits)[0];
  if (bestDirectAnswer && !selected.some((hit) => hitKey(hit) === hitKey(bestDirectAnswer))) {
    const retained = selected.slice(0, MAX_KNOWLEDGE_HITS - 1);
    selected.splice(0, selected.length, bestDirectAnswer, ...retained);
    seen.clear();
    selected.forEach((hit) => seen.add(hitKey(hit)));
  }

  for (const hit of hits) {
    add(hit);
  }
  return selected.slice(0, MAX_KNOWLEDGE_HITS);
}

function compareKnowledgeHits(left: Record<string, unknown>, right: Record<string, unknown>): number {
  const leftDirect = isDirectAnswerHit(left) ? 1 : 0;
  const rightDirect = isDirectAnswerHit(right) ? 1 : 0;
  if (leftDirect !== rightDirect) {
    return rightDirect - leftDirect;
  }
  const leftFact = isHighConfidenceFactHit(left) ? 1 : 0;
  const rightFact = isHighConfidenceFactHit(right) ? 1 : 0;
  if (leftFact !== rightFact) {
    return rightFact - leftFact;
  }
  return scoreOf(right) - scoreOf(left);
}

function isDirectAnswerHit(hit: Record<string, unknown>): boolean {
  return Boolean(normalizedText(hit.direct_answer)) && answerModeOf(hit) === "direct_answer";
}

function isHighConfidenceFactHit(hit: Record<string, unknown>): boolean {
  const kind = knowledgeKindOf(hit);
  return scoreOf(hit) >= DIRECT_ANSWER_SCORE_THRESHOLD && (kind === "business_fact" || kind === "faq");
}

function answerModeOf(hit: Record<string, unknown>): string {
  const metadata = isRecord(hit.metadata) ? hit.metadata : {};
  return textOrEmpty(hit.answer_mode || metadata.answer_mode);
}

function knowledgeKindOf(hit: Record<string, unknown>): string {
  const metadata = isRecord(hit.metadata) ? hit.metadata : {};
  return textOrEmpty(hit.knowledge_kind || metadata.knowledge_kind);
}

function factStatusOf(hit: Record<string, unknown>): string {
  const metadata = isRecord(hit.metadata) ? hit.metadata : {};
  return textOrEmpty(hit.fact_status || metadata.fact_status);
}

function scoreOf(hit: Record<string, unknown>): number {
  const raw = Number(hit.score);
  return Number.isFinite(raw) ? raw : 0;
}

function hitKey(hit: Record<string, unknown>): string {
  return [
    textOrEmpty(hit.item_key),
    textOrEmpty(hit.published_version),
    textOrEmpty(hit.chunk_index),
    textOrEmpty(hit.title),
  ].join(":");
}

function normalizedText(value: unknown): string {
  return textOrEmpty(value).replace(/\s+/g, " ").trim();
}

function textOrEmpty(value: unknown): string {
  if (value === null || value === undefined) {
    return "";
  }
  return sanitizeRuntimeText(String(value));
}

function arrayOfStrings(value: unknown): Array<string> {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => normalizedText(item)).filter(Boolean);
}

function recordPreview(value: unknown, limit: number): Record<string, unknown> {
  if (!isRecord(value)) {
    return {};
  }
  const output: Record<string, unknown> = {};
  for (const [key, entry] of Object.entries(value).slice(0, limit)) {
    output[sanitizeRuntimeText(key)] = typeof entry === "number" ? Math.round(entry * 1000) / 1000 : sanitizeRuntimeText(String(entry));
  }
  return output;
}

function jsonPreview(value: unknown, limit: number): string {
  try {
    return truncate(sanitizeRuntimeText(JSON.stringify(value)), limit);
  } catch {
    return "{}";
  }
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
