import assert from "node:assert/strict";
import test from "node:test";
import { compilePrompt } from "../src/prompt-compiler.js";
import type { ReplyRequest } from "../src/reply-contract.js";

test("prompt compiler keeps latency profile compact and strict", () => {
  const request: ReplyRequest = {
    login: {
      type: "chatgptAuthTokens",
      accessToken: "test-token",
      chatgptAccountId: "acct",
      chatgptPlanType: "plus",
    },
    body: "Where is my parcel? ".repeat(80),
    messages: [
      { role: "user", content: "old context ".repeat(80) },
      { role: "assistant", content: "older reply ".repeat(80) },
      { role: "user", content: "recent context ".repeat(80) },
    ],
    contract: "speedaf_webchat_fast_reply_v1",
    tracking_fact_summary: null,
    tracking_fact_evidence_present: false,
    persona_context: {
      profile_key: "default.website.en",
      name: "Default WebChat",
      summary: "Warm, concise, and clear.",
      content_json: { escalation: "Escalate compensation requests." },
    },
    knowledge_context: {
      hits: [
        {
          title: "Address change policy",
          text: "Customers may request address changes before dispatch. After dispatch, support must verify carrier options.",
        },
      ],
    },
    safety_policy: {
      tracking_truth_boundary: "Knowledge is not live shipment evidence.",
    },
    tenant_id: "default",
    channel_key: "website",
    session_id: "session",
  };

  const prompt = compilePrompt(request);

  assert.ok(prompt.developerInstructions.split(/\s+/).length <= 35);
  assert.ok(prompt.userText.length <= 1200);
  assert.match(prompt.userText, /strict JSON|JSON schema/);
  assert.match(prompt.userText, /Tracking evidence: absent/);
  assert.match(prompt.userText, /Persona:/);
  assert.match(prompt.userText, /Address change policy/);
  assert.match(prompt.userText, /Knowledge is not shipment tracking evidence/);
  assert.doesNotMatch(prompt.userText, /older reply/);
  assert.doesNotMatch(prompt.userText, /old context/);
});
