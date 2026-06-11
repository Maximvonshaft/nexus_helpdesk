import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import type { ConnectorMode, SidecarConfig } from "./types.js";

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

function modeEnv(): ConnectorMode {
  const mode = (process.env.WA_SIDECAR_CONNECTOR_MODE || "mock").trim().toLowerCase();
  if (mode !== "mock" && mode !== "baileys") {
    throw new Error("WA_SIDECAR_CONNECTOR_MODE must be mock or baileys");
  }
  return mode;
}

export function loadConfig(): SidecarConfig {
  const sessionRoot = resolve(process.env.WHATSAPP_SESSION_ROOT || "/data/whatsapp-sessions");
  mkdirSync(sessionRoot, { recursive: true, mode: 0o700 });
  return {
    port: intEnv("WA_SIDECAR_PORT", 18793),
    mode: modeEnv(),
    sessionRoot,
    internalToken: requireEnv("WA_SIDECAR_INTERNAL_TOKEN"),
    backendUrl: requireEnv("NEXUS_BACKEND_URL").replace(/\/+$/, ""),
    connectorKey: requireEnv("NEXUS_CONNECTOR_KEY"),
    connectorHmacSecret: requireEnv("NEXUS_CONNECTOR_HMAC_SECRET"),
    callbackTimeoutMs: intEnv("NEXUS_CALLBACK_TIMEOUT_MS", 8000),
    logLevel: process.env.LOG_LEVEL || "info"
  };
}
