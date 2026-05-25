import assert from "node:assert/strict";
import test from "node:test";
import { parseStrictReply, validateReplyRequest } from "../src/reply-contract.js";
import { RuntimeError } from "../src/errors.js";

test("strict reply parser accepts Nexus reply contract", () => {
  const parsed = parseStrictReply(
    JSON.stringify({
      reply: "Hello",
      intent: "greeting",
      tracking_number: null,
      handoff_required: false,
      handoff_reason: null,
      recommended_agent_action: null,
    }),
  );

  assert.equal(parsed.intent, "greeting");
});

test("strict reply parser fails closed on non JSON", () => {
  assert.throws(() => parseStrictReply("not-json"), RuntimeError);
});

test("request validation rejects missing token", () => {
  assert.throws(
    () => validateReplyRequest({ login: { type: "chatgptAuthTokens" }, body: "hello" }),
    /missing_access_token/,
  );
});

test("request validation sanitizes runtime context before prompt compilation", () => {
  const request = validateReplyRequest({
    login: { type: "chatgptAuthTokens", accessToken: "token", chatgptAccountId: "acct" },
    body: "provider_runtime http://localhost:8000 api_key=secret-value",
    messages: [{ role: "user", content: "OpenClaw bridge codex_app_server" }],
    persona_context: { summary: "system prompt" },
  });

  assert.doesNotMatch(request.body, /provider_runtime|localhost|secret-value/i);
  assert.doesNotMatch(request.messages[0]?.content || "", /OpenClaw|bridge|codex_app_server/i);
  assert.doesNotMatch(String(request.persona_context?.summary || ""), /system prompt/i);
});
