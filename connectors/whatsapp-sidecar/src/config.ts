import { chmodSync, mkdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { ConnectorMode, FromMeInboundMode, SidecarConfig } from "./types.js";

function env(name: string): string | undefined {
  const value = process.env[name]?.trim();
  return value || undefined;
}

function secret(params: {
  valueEnv: string;
  fileEnv: string;
  defaultFile: string;
  production: boolean;
}): string {
  const filePath = resolve(env(params.fileEnv) || params.defaultFile);
  try {
    const value = readFileSync(filePath, "utf8").trim();
    if (value) return value;
  } catch {
    // The adapter boundary below supplies the precise missing-secret error.
  }
  const value = env(params.valueEnv);
  if (value && params.production) {
    throw new Error(
      `production prohibits plain text ${params.valueEnv}; use ${params.fileEnv}`
    );
  }
  if (!value) {
    throw new Error(`${params.fileEnv} is required`);
  }
  return value;
}

function intEnv(name: string, fallback: number, minimum = 1, maximum = Number.MAX_SAFE_INTEGER): number {
  const raw = env(name);
  const parsed = raw ? Number.parseInt(raw, 10) : fallback;
  if (!Number.isFinite(parsed) || parsed < minimum || parsed > maximum) {
    throw new Error(`${name} must be between ${minimum} and ${maximum}`);
  }
  return parsed;
}

function floatEnv(name: string, fallback: number, minimum: number, maximum: number): number {
  const raw = env(name);
  const parsed = raw ? Number.parseFloat(raw) : fallback;
  if (!Number.isFinite(parsed) || parsed < minimum || parsed > maximum) {
    throw new Error(`${name} must be between ${minimum} and ${maximum}`);
  }
  return parsed;
}

function boolEnv(name: string, fallback: boolean): boolean {
  const raw = env(name)?.toLowerCase();
  if (!raw) return fallback;
  if (["1", "true", "yes", "on"].includes(raw)) return true;
  if (["0", "false", "no", "off"].includes(raw)) return false;
  throw new Error(`${name} must be a boolean`);
}

function modeEnv(production: boolean): ConnectorMode {
  const mode = (env("WA_SIDECAR_CONNECTOR_MODE") || (production ? "baileys" : "mock")).toLowerCase();
  if (mode !== "mock" && mode !== "baileys") {
    throw new Error("WA_SIDECAR_CONNECTOR_MODE must be mock or baileys");
  }
  if (production && mode !== "baileys") {
    throw new Error("production requires WA_SIDECAR_CONNECTOR_MODE=baileys");
  }
  return mode;
}

function fromMeModeEnv(): FromMeInboundMode {
  const mode = (env("WA_SIDECAR_FROM_ME_MODE") || "ignore").toLowerCase();
  if (mode !== "ignore" && mode !== "store_only" && mode !== "test_visitor") {
    throw new Error(
      "WA_SIDECAR_FROM_ME_MODE must be ignore, store_only, or test_visitor"
    );
  }
  return mode;
}

function secureDirectory(path: string): string {
  const resolved = resolve(path);
  mkdirSync(resolved, { recursive: true, mode: 0o700 });
  chmodSync(resolved, 0o700);
  return resolved;
}

export function loadConfig(): SidecarConfig {
  const production = (env("APP_ENV") || "development").toLowerCase() === "production";
  const sessionRoot = secureDirectory(env("WHATSAPP_SESSION_ROOT") || "/data/whatsapp-sessions");
  const callbackSpoolRoot = secureDirectory(
    env("WHATSAPP_CALLBACK_SPOOL_ROOT") || "/data/whatsapp-callback-spool"
  );
  const backendUrl = env("NEXUS_BACKEND_URL")?.replace(/\/+$/, "");
  if (!backendUrl || !/^https?:\/\//.test(backendUrl)) {
    throw new Error("NEXUS_BACKEND_URL must be an http(s) URL");
  }

  return {
    port: intEnv("WA_SIDECAR_PORT", 18793, 1, 65535),
    mode: modeEnv(production),
    production,
    sessionRoot,
    callbackSpoolRoot,
    internalToken: secret({
      valueEnv: "WA_SIDECAR_INTERNAL_TOKEN",
      fileEnv: "WA_SIDECAR_INTERNAL_TOKEN_FILE",
      defaultFile: "/run/nexus/whatsapp_baileys_sidecar_token",
      production
    }),
    backendUrl,
    connectorKey: secret({
      valueEnv: "NEXUS_CONNECTOR_KEY",
      fileEnv: "NEXUS_CONNECTOR_KEY_FILE",
      defaultFile: "/run/nexus/whatsapp_connector_key",
      production
    }),
    connectorHmacSecret: secret({
      valueEnv: "NEXUS_CONNECTOR_HMAC_SECRET",
      fileEnv: "NEXUS_CONNECTOR_HMAC_SECRET_FILE",
      defaultFile: "/run/nexus/whatsapp_connector_hmac_secret",
      production
    }),
    callbackTimeoutMs: intEnv("NEXUS_CALLBACK_TIMEOUT_MS", 8000, 1000, 60000),
    callbackRetryIntervalMs: intEnv("WA_SIDECAR_CALLBACK_RETRY_INTERVAL_MS", 5000, 1000, 300000),
    reconcileIntervalMs: intEnv("WA_SIDECAR_RECONCILE_INTERVAL_MS", 15000, 5000, 300000),
    credentialPersistenceTimeoutMs: intEnv(
      "WA_SIDECAR_CREDENTIAL_PERSISTENCE_TIMEOUT_MS",
      15000,
      1000,
      60000
    ),
    qrTtlMs: intEnv("WA_SIDECAR_QR_TTL_MS", 60000, 15000, 180000),
    reconnectInitialMs: intEnv("WA_SIDECAR_RECONNECT_INITIAL_MS", 2000, 250, 30000),
    reconnectMaxMs: intEnv("WA_SIDECAR_RECONNECT_MAX_MS", 30000, 1000, 300000),
    reconnectMaxAttempts: intEnv("WA_SIDECAR_RECONNECT_MAX_ATTEMPTS", 12, 1, 100),
    reconnectJitter: floatEnv("WA_SIDECAR_RECONNECT_JITTER", 0.25, 0, 1),
    idempotencyTtlMs: intEnv("WA_SIDECAR_IDEMPOTENCY_TTL_MS", 86400000, 60000, 604800000),
    logLevel: env("LOG_LEVEL") || "info",
    browserName: env("WA_SIDECAR_BROWSER_NAME") || "NexusDesk",
    allowFromMeInbound: boolEnv("WA_SIDECAR_ALLOW_FROM_ME_INBOUND", false),
    fromMeMode: fromMeModeEnv(),
    fromMeTestPrefix: env("WA_SIDECAR_FROM_ME_TEST_PREFIX") || "NEXUS_SELF_INBOUND_TEST"
  };
}
