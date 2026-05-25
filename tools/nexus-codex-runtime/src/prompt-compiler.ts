import { sanitizeRuntimeText, type ReplyRequest } from "./reply-contract.js";

export type CompiledPrompt = {
  developerInstructions: string;
  userText: string;
};

const DEVELOPER_INSTRUCTIONS =
  "NexusDesk WebChat. Strict JSON only. Use persona/knowledge for tone, FAQ, SOP, policy only. No markdown, tools, runtime, tokens, or unsupported parcel-status claims. Without tracking evidence ask for tracking number. Reply under 600 chars.";

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
    : "Tracking evidence: absent. Do not claim parcel status.";
  const persona = formatPersonaContext(request.persona_context);
  const knowledge = formatKnowledgeContext(request.knowledge_context);
  const safety = request.safety_policy
    ? "Knowledge is not shipment tracking evidence. Live parcel status requires trusted tracking evidence."
    : "";
  const body = sanitizeRuntimeText(request.body || "");
  const schema =
    '{"reply":"string","intent":"greeting|tracking|tracking_missing_number|tracking_unresolved|complaint|address_change|handoff|other","tracking_number":"string|null","handoff_required":boolean,"handoff_reason":"string|null","recommended_agent_action":"string|null"}';
  const userText = truncate(
    sanitizeRuntimeText(
    [
      `Contract=${sanitizeRuntimeText(request.contract || "speedaf_webchat_fast_reply_v1")}`,
      facts,
      persona ? `Persona:\n${persona}` : "",
      knowledge ? `Knowledge (policy/FAQ/SOP only):\n${knowledge}` : "",
      safety,
      history ? `Context:\n${history}` : "",
      `Customer:\n${truncate(body.replace(/\s+/g, " ").trim(), 220)}`,
      `JSON schema: ${schema}`,
    ]
      .filter(Boolean)
      .join("\n\n"),
    ),
    1200,
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
  const content = isRecord(value.content_json) ? sanitizeRuntimeText(JSON.stringify(value.content_json)) : "";
  return truncate(sanitizeRuntimeText([key || name, summary, content].filter(Boolean).join(" | ")), 260);
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
