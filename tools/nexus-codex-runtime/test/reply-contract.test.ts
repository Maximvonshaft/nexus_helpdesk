import assert from "node:assert/strict";
import test from "node:test";
import {
  enforcePersonaRules,
  extractPersonaVisiblePrefix,
  parseStrictReply,
  validateReplyRequest,
} from "../src/reply-contract.js";
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
    body: "provider_runtime private runtime endpoint private_value=redacted-by-test",
    messages: [{ role: "user", content: "ExternalChannel bridge codex_app_server" }],
    persona_context: { summary: "system prompt" },
  });

  assert.doesNotMatch(request.body, /provider_runtime|redacted-by-test/i);
  assert.doesNotMatch(request.messages[0]?.content || "", /ExternalChannel|bridge|codex_app_server/i);
  assert.doesNotMatch(String(request.persona_context?.summary || ""), /system prompt/i);
});

test("persona visible prefix is extracted from sanitized context", () => {
  const request = validateReplyRequest({
    login: { type: "chatgptAuthTokens", accessToken: "token", chatgptAccountId: "acct" },
    body: "hello",
    messages: [],
    persona_context: {
      content_json: {
        must_prefix: "SPEEDY_PERSONA_OK",
      },
    },
  });

  assert.equal(extractPersonaVisiblePrefix(request.persona_context), "SPEEDY_PERSONA_OK");
});

test("deterministic persona enforcement prefixes replies when model ignores must_prefix", () => {
  const request = validateReplyRequest({
    login: { type: "chatgptAuthTokens", accessToken: "token", chatgptAccountId: "acct" },
    body: "hello",
    messages: [],
    persona_context: {
      content_json: {
        must_prefix: "SPEEDY_PERSONA_OK",
      },
    },
  });
  const reply = parseStrictReply(
    JSON.stringify({
      reply: "Hi there! How can I help?",
      intent: "greeting",
      tracking_number: null,
      handoff_required: false,
      handoff_reason: null,
      recommended_agent_action: null,
    }),
  );

  const enforced = enforcePersonaRules(reply, request);

  assert.equal(enforced.reply, "SPEEDY_PERSONA_OK Hi there! How can I help?");
  assert.equal(enforced.intent, "greeting");
});

test("deterministic persona enforcement does not duplicate existing prefix", () => {
  const request = validateReplyRequest({
    login: { type: "chatgptAuthTokens", accessToken: "token", chatgptAccountId: "acct" },
    body: "hello",
    messages: [],
    persona_context: {
      content_json: {
        must_prefix: "SPEEDY_PERSONA_OK",
      },
    },
  });
  const reply = parseStrictReply(
    JSON.stringify({
      reply: "SPEEDY_PERSONA_OK Already applied.",
      intent: "greeting",
      tracking_number: null,
      handoff_required: false,
      handoff_reason: null,
      recommended_agent_action: null,
    }),
  );

  const enforced = enforcePersonaRules(reply, request);

  assert.equal(enforced.reply, "SPEEDY_PERSONA_OK Already applied.");
});

test("deterministic persona enforcement keeps 600 char reply limit", () => {
  const request = validateReplyRequest({
    login: { type: "chatgptAuthTokens", accessToken: "token", chatgptAccountId: "acct" },
    body: "hello",
    messages: [],
    persona_context: {
      content_json: {
        must_prefix: "SPEEDY_PERSONA_OK",
      },
    },
  });
  const reply = parseStrictReply(
    JSON.stringify({
      reply: "A".repeat(590),
      intent: "greeting",
      tracking_number: null,
      handoff_required: false,
      handoff_reason: null,
      recommended_agent_action: null,
    }),
  );

  const enforced = enforcePersonaRules(reply, request);

  assert.ok(enforced.reply.startsWith("SPEEDY_PERSONA_OK"));
  assert.ok(enforced.reply.length <= 600);
});
