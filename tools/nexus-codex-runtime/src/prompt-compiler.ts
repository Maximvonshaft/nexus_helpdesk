import type { ReplyRequest } from "./reply-contract.js";

export type CompiledPrompt = {
  developerInstructions: string;
  userText: string;
};

const DEVELOPER_INSTRUCTIONS =
  "You are NexusDesk WebChat AI. Return JSON only matching the required schema. No markdown, tools, shell, browser, runtime details, token details, or unsupported parcel status claims. If tracking evidence is absent, do not invent shipment status. Keep reply under 600 characters.";

export function compilePrompt(request: ReplyRequest): CompiledPrompt {
  const history = request.messages
    .slice(-3)
    .map((message) => {
      const role = typeof message.role === "string" ? message.role.slice(0, 16) : "user";
      const content = typeof message.content === "string" ? message.content : "";
      return `${role}: ${content}`;
    })
    .join("\n");
  const facts = request.tracking_fact_evidence_present
    ? `Tracking evidence: ${request.tracking_fact_summary || "present"}`
    : "Tracking evidence: absent. Do not claim parcel status.";
  const body = request.body || "";
  const schema =
    '{"reply":"string","intent":"greeting|tracking|tracking_missing_number|tracking_unresolved|complaint|address_change|handoff|other","tracking_number":null,"handoff_required":false,"handoff_reason":null,"recommended_agent_action":null}';
  const userText = truncate(
    [
      `Contract: ${request.contract || "speedaf_webchat_fast_reply_v1"}`,
      facts,
      history ? `Recent context:\n${history}` : "",
      `Customer message:\n${body}`,
      `Return only strict JSON shaped exactly like: ${schema}`,
    ]
      .filter(Boolean)
      .join("\n\n"),
    1500,
  );
  return {
    developerInstructions: truncateWords(DEVELOPER_INSTRUCTIONS, 120),
    userText,
  };
}

function truncate(value: string, max: number): string {
  return value.length <= max ? value : value.slice(0, max - 3) + "...";
}

function truncateWords(value: string, maxWords: number): string {
  const words = value.split(/\s+/);
  return words.length <= maxWords ? value : words.slice(0, maxWords).join(" ");
}
