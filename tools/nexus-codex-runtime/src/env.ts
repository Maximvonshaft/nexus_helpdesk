import { tmpdir } from "node:os";
import { join } from "node:path";
import { stableHash } from "./redaction.js";

export type RuntimeConfig = {
  enabled: boolean;
  host: string;
  port: number;
  model: string;
  threadMode: "ephemeral";
  clientCacheTtlSeconds: number;
  queueTimeoutMs: number;
  replyTimeoutMs: number;
  maxConcurrency: number;
  codexCommand: string;
  codexArgs: string[];
  stateDir: string;
  runtimeStartOptionsHash: string;
  release: {
    gitSha: string;
    imageTag: string;
    appVersion: string;
    buildTime: string;
  };
};

export function loadConfig(env: NodeJS.ProcessEnv = process.env): RuntimeConfig {
  const codexCommand = env.CODEX_APPSERVER_COMMAND || "codex";
  const codexArgs = splitArgs(env.CODEX_APPSERVER_ARGS || "app-server --listen stdio://");
  const startOptions = {
    command: codexCommand,
    args: codexArgs,
    clearEnv: ["CODEX_API_KEY", "OPENAI_API_KEY", "OPENAI_ACCESS_TOKEN", "CODEX_ACCESS_TOKEN", "OPENCLAW_HOME"],
  };
  return {
    enabled: parseBool(env.CODEX_APPSERVER_RUNTIME_ENABLED ?? "true"),
    host: env.CODEX_APPSERVER_HOST || "0.0.0.0",
    port: parseIntEnv(env.CODEX_APPSERVER_PORT, 18810, 1, 65535),
    model: env.CODEX_APPSERVER_MODEL || "openai/gpt-5.5",
    threadMode: "ephemeral",
    clientCacheTtlSeconds: parseIntEnv(env.CODEX_APPSERVER_CLIENT_CACHE_TTL_SECONDS, 1800, 1, 86400),
    queueTimeoutMs: parseIntEnv(env.CODEX_APPSERVER_QUEUE_TIMEOUT_MS, 200, 1, 60000),
    replyTimeoutMs: parseIntEnv(env.CODEX_APPSERVER_REPLY_TIMEOUT_MS, 8000, 100, 120000),
    maxConcurrency: parseIntEnv(env.CODEX_APPSERVER_MAX_CONCURRENCY, 4, 1, 64),
    codexCommand,
    codexArgs,
    stateDir: env.CODEX_APPSERVER_STATE_DIR || join(tmpdir(), "nexus-codex-runtime"),
    runtimeStartOptionsHash: stableHash(startOptions),
    release: {
      gitSha: env.GIT_SHA || "unknown",
      imageTag: env.IMAGE_TAG || "unknown",
      appVersion: env.APP_VERSION || "unknown",
      buildTime: env.BUILD_TIME || "unknown",
    },
  };
}

function parseBool(value: string): boolean {
  return ["1", "true", "yes", "on"].includes(value.trim().toLowerCase());
}

function parseIntEnv(value: string | undefined, fallback: number, min: number, max: number): number {
  const parsed = Number.parseInt(value || "", 10);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.max(min, Math.min(max, parsed));
}

function splitArgs(value: string): string[] {
  const matches = value.match(/(?:[^\s"]+|"[^"]*")+/g) || [];
  return matches.map((item) => item.replace(/^"|"$/g, ""));
}
