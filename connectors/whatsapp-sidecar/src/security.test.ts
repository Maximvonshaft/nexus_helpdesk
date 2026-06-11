import assert from "node:assert/strict";
import test from "node:test";
import { connectorHeaders, connectorSignature, isAuthorized } from "./security.js";

test("bearer auth uses exact token comparison", () => {
  assert.equal(isAuthorized("Bearer secret", "secret"), true);
  assert.equal(isAuthorized("Bearer wrong", "secret"), false);
  assert.equal(isAuthorized(undefined, "secret"), false);
});

test("connector headers include hmac over timestamp and raw body", () => {
  const rawBody = JSON.stringify({ ok: true });
  const headers = connectorHeaders({
    accountId: "wa-main",
    connectorKey: "key",
    hmacSecret: "secret",
    rawBody,
    timestamp: "2026-06-11T12:00:00.000Z"
  });
  assert.equal(headers["x-nexus-account-id"], "wa-main");
  assert.equal(headers["x-nexus-connector-key"], "key");
  assert.equal(headers["x-nexus-signature"], connectorSignature("secret", "2026-06-11T12:00:00.000Z", rawBody));
});
