import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import type { ConnectorMode, FromMeInboundMode, SidecarConfig } from "./types.js";

function requireEnv(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`${name} is required`);
  }
  return value;
}

function intEnv(name: string, fallback: number): number {
  const raw = process.env[name]?.trim();
  if (!raw) return fallback;
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`${name} must be a positive integer`);
  }
  return parsed;
}

function boolEnv(name: string, fallback: boolean): boolean {
  const raw = process.env[name]?.trim().toLowerCase();
  if (!raw) return fallback;
  if (["1", "true", "yes", "on"].includes(raw)) return true;
  if (["0", "false", "no", "off"].includes(raw)) return false;
  throw new Error(`${name} must be a boolean`);
}

function modeEnv(): ConnectorMode {
  const mode = (process.env.WA_SIDECAR_CONNECTOR_MODE || "mock").trim().toLowerCase();
  if (mode !== "mock" && mode !== "baileys") {
    throw new Error("WA_SIDECAR_CONNECTOR_MODE must be mock or baileys");
  }
  return mode;
}

function fromMeModeEnv(): FromMeInboundMode {
  const mode = (process.env.WA_SIDECAR_FROM_ME_MODE || "ignore").trim().toLowerCase();
  if (mode !== "ignore" && mode !== "store_only" && mode !== "test_visitor") {
    throw new Error("WA_SIDECAR_FROM_ME_MODE must be ignore, store_only, or test_visitor");
  }
  return mode;
}

function listEnv(name: string): string[] {
  return (process.env[name] || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function loadConfig(): SidecarConfig {
  const sessionRoot = resolve(process.env.WHATSAPP_SESSION_ROOT || "/data/whatsapp-sessions");
  mkdirSync(sessionRoot, { recursive: true, mode: 0o700 });
  return {
    port: intEnv("WA_SIDECAR_PORT", 18793),
    mode: modeEnv(),
    sessionRoot,
    autoStartAccounts: listEnv("WA_SIDECAR_AUTO_START_ACCOUNTS"),
    internalToken: requireEnv("WA_SIDECAR_INTERNAL_TOKEN"),
    backendUrl: requireEnv("NEXUS_BACKEND_URL").replace(/\/+$/, ""),
    connectorKey: requireEnv("NEXUS_CONNECTOR_KEY"),
    connectorHmacSecret: requireEnv("NEXUS_CONNECTOR_HMAC_SECRET"),
    callbackTimeoutMs: intEnv("NEXUS_CALLBACK_TIMEOUT_MS", 8000),
    logLevel: process.env.LOG_LEVEL || "info",
    browserPlatform: process.env.WA_SIDECAR_BROWSER_PLATFORM?.trim() || "Ubuntu",
    browserName: process.env.WA_SIDECAR_BROWSER_NAME?.trim() || "NexusDesk",
    browserVersion: process.env.WA_SIDECAR_BROWSER_VERSION?.trim() || "22.04.4",
    keepAliveIntervalMs: intEnv("WA_SIDECAR_KEEPALIVE_INTERVAL_MS", 25_000),
    connectTimeoutMs: intEnv("WA_SIDECAR_CONNECT_TIMEOUT_MS", 60_000),
    defaultQueryTimeoutMs: intEnv("WA_SIDECAR_DEFAULT_QUERY_TIMEOUT_MS", 60_000),
    operationTimeoutMs: intEnv("WA_SIDECAR_OPERATION_TIMEOUT_MS", 60_000),
    qrTtlMs: intEnv("WA_SIDECAR_QR_TTL_MS", 120_000),
    reconnectBaseDelayMs: intEnv("WA_SIDECAR_RECONNECT_BASE_DELAY_MS", 2_000),
    reconnectMaxDelayMs: intEnv("WA_SIDECAR_RECONNECT_MAX_DELAY_MS", 30_000),
    reconnectMaxAttempts: intEnv("WA_SIDECAR_RECONNECT_MAX_ATTEMPTS", 20),
    allowFromMeInbound: boolEnv("WA_SIDECAR_ALLOW_FROM_ME_INBOUND", false),
    fromMeMode: fromMeModeEnv(),
    fromMeTestPrefix: process.env.WA_SIDECAR_FROM_ME_TEST_PREFIX?.trim() || "NEXUS_SELF_INBOUND_TEST"
  };
}
