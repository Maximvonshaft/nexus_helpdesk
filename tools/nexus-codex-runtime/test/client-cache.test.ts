import assert from "node:assert/strict";
import test from "node:test";
import { clientCacheKey } from "../src/client-cache.js";

test("client cache key separates accounts and token fingerprints", () => {
  const base = {
    tenantId: "tenant-a",
    model: "openai/gpt-5.5",
    runtimeStartOptionsHash: "runtime-a",
  };
  const first = clientCacheKey({
    ...base,
    login: {
      type: "chatgptAuthTokens",
      accessToken: "token-one",
      chatgptAccountId: "acct-one",
      chatgptPlanType: "plus",
    },
  });
  const second = clientCacheKey({
    ...base,
    login: {
      type: "chatgptAuthTokens",
      accessToken: "token-two",
      chatgptAccountId: "acct-one",
      chatgptPlanType: "plus",
    },
  });
  const third = clientCacheKey({
    ...base,
    login: {
      type: "chatgptAuthTokens",
      accessToken: "token-one",
      chatgptAccountId: "acct-two",
      chatgptPlanType: "plus",
    },
  });

  assert.notEqual(first, second);
  assert.notEqual(first, third);
});
