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
    tenant_id: "default",
    channel_key: "website",
    session_id: "session",
  };

  const prompt = compilePrompt(request);

  assert.ok(prompt.developerInstructions.split(/\s+/).length <= 35);
  assert.ok(prompt.userText.length <= 720);
  assert.match(prompt.userText, /strict JSON|JSON schema/);
  assert.match(prompt.userText, /Tracking evidence: absent/);
  assert.doesNotMatch(prompt.userText, /older reply/);
  assert.doesNotMatch(prompt.userText, /old context/);
});
