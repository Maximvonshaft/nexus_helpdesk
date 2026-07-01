import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { loadConfig } from "./config.js";

const ENV_KEYS = [
  "WA_SIDECAR_PORT",
  "WA_SIDECAR_CONNECTOR_MODE",
  "WA_SIDECAR_AUTO_START_ACCOUNTS",
  "WA_SIDECAR_INTERNAL_TOKEN",
  "NEXUS_BACKEND_URL",
  "NEXUS_CONNECTOR_KEY",
  "NEXUS_CONNECTOR_HMAC_SECRET",
  "NEXUS_CALLBACK_TIMEOUT_MS",
  "LOG_LEVEL",
  "WA_SIDECAR_BAILEYS_LOG_LEVEL",
  "WA_SIDECAR_KEEP_ALIVE_INTERVAL_MS",
  "WA_SIDECAR_KEEPALIVE_INTERVAL_MS",
  "WHATSAPP_SESSION_ROOT"
];

function withEnv(env: Record<string, string>, fn: () => void): void {
  const previous = new Map(ENV_KEYS.map((key) => [key, process.env[key]]));
  for (const key of ENV_KEYS) {
    delete process.env[key];
  }
  Object.assign(process.env, env);
  try {
    fn();
  } finally {
    for (const key of ENV_KEYS) {
      const value = previous.get(key);
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
  }
}

function baseEnv(): Record<string, string> {
  return {
    WA_SIDECAR_INTERNAL_TOKEN: "test-token",
    NEXUS_BACKEND_URL: "http://backend.test/",
    NEXUS_CONNECTOR_KEY: "connector-key",
    NEXUS_CONNECTOR_HMAC_SECRET: "connector-secret",
    WHATSAPP_SESSION_ROOT: mkdtempSync(join(tmpdir(), "nexus-wa-config-test-"))
  };
}

test("loadConfig reads hardened sidecar defaults and aliases", () => {
  const env: Record<string, string> = {
    ...baseEnv(),
    WA_SIDECAR_CONNECTOR_MODE: "baileys",
    WA_SIDECAR_AUTO_START_ACCOUNTS: " wa-one, wa-two ",
    WA_SIDECAR_KEEP_ALIVE_INTERVAL_MS: "12345"
  };

  try {
    withEnv(env, () => {
      const config = loadConfig();

      assert.equal(config.mode, "baileys");
      assert.deepEqual(config.autoStartAccounts, ["wa-one", "wa-two"]);
      assert.equal(config.backendUrl, "http://backend.test");
      assert.equal(config.keepAliveIntervalMs, 12345);
      assert.equal(config.baileysLogLevel, "silent");
    });
  } finally {
    rmSync(env.WHATSAPP_SESSION_ROOT, { recursive: true, force: true });
  }
});

test("loadConfig keeps legacy keepalive spelling as fallback", () => {
  const env: Record<string, string> = {
    ...baseEnv(),
    WA_SIDECAR_KEEPALIVE_INTERVAL_MS: "23456",
    WA_SIDECAR_BAILEYS_LOG_LEVEL: "warn"
  };

  try {
    withEnv(env, () => {
      const config = loadConfig();

      assert.equal(config.keepAliveIntervalMs, 23456);
      assert.equal(config.baileysLogLevel, "warn");
    });
  } finally {
    rmSync(env.WHATSAPP_SESSION_ROOT, { recursive: true, force: true });
  }
});
