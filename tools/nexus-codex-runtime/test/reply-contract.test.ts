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
