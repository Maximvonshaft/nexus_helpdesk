import assert from "node:assert/strict";
import test from "node:test";
import { clientCacheKey, loginFingerprint } from "../src/client-cache.js";

test("client cache key separates accounts without splitting refreshed access tokens", () => {
  const base = {
    tenantId: "tenant-a",
    model: "gpt-5.5",
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

  assert.equal(first, second);
  assert.notEqual(first, third);
});

test("login fingerprint changes when access token rotates", () => {
  const base = {
    type: "chatgptAuthTokens" as const,
    chatgptAccountId: "acct-one",
    chatgptPlanType: "plus",
  };

  assert.notEqual(
    loginFingerprint({ ...base, accessToken: "token-one" }),
    loginFingerprint({ ...base, accessToken: "token-two" }),
  );
});
