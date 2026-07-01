import { tmpdir } from "node:os";
import { join } from "node:path";
import { stableHash } from "./redaction.js";

const DEFAULT_MODEL = "gpt-5.3-codex-spark";
const DEFAULT_QUEUE_TIMEOUT_MS = 750;
const DEFAULT_REPLY_TIMEOUT_MS = 8000;
const DEFAULT_MAX_CONCURRENCY = 4;
const DEFAULT_PERFORMANCE_PROFILE = "webchat_fast";
const DEFAULT_REASONING_EFFORT = "low";
const DEFAULT_SERVICE_TIER = "priority";

export type RuntimeConfig = {
  enabled: boolean;
  host: string;
  port: number;
  model: string;
  performanceProfile: string;
  serviceTier: string | null;
  reasoningEffort: string | null;
  threadMode: "ephemeral";
  clientCacheTtlSeconds: number;
  queueTimeoutMs: number;
  replyTimeoutMs: number;
  maxConcurrency: number;
  codexCommand: string;
  codexArgs: string[];
  stateDir: string;
  workDir: string;
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
  const stateDir = env.CODEX_APPSERVER_STATE_DIR || join(tmpdir(), "nexus-codex-runtime");
  const performanceProfile = env.CODEX_APPSERVER_PERFORMANCE_PROFILE || DEFAULT_PERFORMANCE_PROFILE;
  const profileDefaults = performanceProfile === "baseline"
    ? { reasoningEffort: null, serviceTier: null }
    : { reasoningEffort: DEFAULT_REASONING_EFFORT, serviceTier: DEFAULT_SERVICE_TIER };
  const startOptions = {
    command: codexCommand,
    args: codexArgs,
    clearEnv: ["CODEX_API_KEY", "OPENAI_API_KEY", "OPENAI_ACCESS_TOKEN", "CODEX_ACCESS_TOKEN", "EXTERNAL_CHANNEL_HOME"],
  };
  return {
    enabled: parseBool(env.CODEX_APPSERVER_RUNTIME_ENABLED ?? "true"),
    host: env.CODEX_APPSERVER_HOST || "0.0.0.0",
    port: parseIntEnv(env.CODEX_APPSERVER_PORT, 18810, 1, 65535),
    model: env.CODEX_APPSERVER_MODEL || DEFAULT_MODEL,
    performanceProfile,
    serviceTier: normalizeServiceTier(env.CODEX_APPSERVER_SERVICE_TIER ?? profileDefaults.serviceTier),
    reasoningEffort: normalizeReasoningEffort(env.CODEX_APPSERVER_REASONING_EFFORT ?? profileDefaults.reasoningEffort),
    threadMode: "ephemeral",
    clientCacheTtlSeconds: parseIntEnv(env.CODEX_APPSERVER_CLIENT_CACHE_TTL_SECONDS, 1800, 1, 86400),
    queueTimeoutMs: parseIntEnv(env.CODEX_APPSERVER_QUEUE_TIMEOUT_MS, DEFAULT_QUEUE_TIMEOUT_MS, 1, 60000),
    replyTimeoutMs: parseIntEnv(env.CODEX_APPSERVER_REPLY_TIMEOUT_MS, DEFAULT_REPLY_TIMEOUT_MS, 100, 120000),
    maxConcurrency: parseIntEnv(env.CODEX_APPSERVER_MAX_CONCURRENCY, DEFAULT_MAX_CONCURRENCY, 1, 64),
    codexCommand,
    codexArgs,
    stateDir,
    workDir: env.CODEX_APPSERVER_WORK_DIR || join(stateDir, "webchat-workdir"),
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

function normalizeReasoningEffort(value: string | null | undefined): string | null {
  const normalized = (value || "").trim().toLowerCase();
  if (!normalized || normalized === "off" || normalized === "none" || normalized === "null") {
    return null;
  }
  if (normalized === "minimal") {
    return "low";
  }
  return ["low", "medium", "high", "xhigh"].includes(normalized) ? normalized : null;
}

function normalizeServiceTier(value: string | null | undefined): string | null {
  const trimmed = (value || "").trim();
  if (!trimmed || ["off", "none", "null"].includes(trimmed.toLowerCase())) {
    return null;
  }
  const normalized = trimmed.toLowerCase();
  if (normalized === "fast" || normalized === "priority") {
    return "priority";
  }
  return normalized === "flex" ? "flex" : trimmed;
}
