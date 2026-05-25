import type { ReplyRequest } from "./reply-contract.js";

export type CompiledPrompt = {
  developerInstructions: string;
  userText: string;
};

const DEVELOPER_INSTRUCTIONS =
  "NexusDesk WebChat. Return strict JSON only. No markdown, tools, runtime, token, or unsupported parcel-status claims. Without tracking evidence, ask for the tracking number. Reply under 600 characters.";

export function compilePrompt(request: ReplyRequest): CompiledPrompt {
  const history = request.messages
    .slice(-1)
    .map((message) => {
      const role = typeof message.role === "string" ? message.role.slice(0, 16) : "user";
      const content = typeof message.content === "string" ? truncate(message.content.replace(/\s+/g, " ").trim(), 90) : "";
      return `${role}: ${content}`;
    })
    .join("\n");
  const facts = request.tracking_fact_evidence_present
    ? `Tracking evidence: ${request.tracking_fact_summary || "present"}`
    : "Tracking evidence: absent. Do not claim parcel status.";
  const body = request.body || "";
  const schema =
    '{"reply":"string","intent":"greeting|tracking|tracking_missing_number|tracking_unresolved|complaint|address_change|handoff|other","tracking_number":"string|null","handoff_required":boolean,"handoff_reason":"string|null","recommended_agent_action":"string|null"}';
  const userText = truncate(
    [
      `Contract=${request.contract || "speedaf_webchat_fast_reply_v1"}`,
      facts,
      history ? `Context:\n${history}` : "",
      `Customer:\n${truncate(body.replace(/\s+/g, " ").trim(), 220)}`,
      `JSON schema: ${schema}`,
    ]
      .filter(Boolean)
      .join("\n\n"),
    720,
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
