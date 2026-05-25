import { mkdirSync } from "node:fs";
import { RuntimeError, classifyAuthFailure } from "./errors.js";
import type { RpcClient } from "./rpc-client.js";
import type { RuntimeConfig } from "./env.js";
import type { ReplyRequest } from "./reply-contract.js";
import { compilePrompt } from "./prompt-compiler.js";
import { TerminalTurnCollector } from "./terminal-turn-collector.js";

export type ThreadRunResult = {
  assistantText: string;
  extractionPath?: string;
  threadStartMs: number;
  turnStartMs: number;
  terminalWaitMs: number;
};

export async function runEphemeralThread(
  client: RpcClient,
  config: RuntimeConfig,
  request: ReplyRequest,
  timeoutMs: number,
): Promise<ThreadRunResult> {
  const prompt = compilePrompt(request);
  mkdirSync(config.workDir, { recursive: true, mode: 0o700 });
  const threadStarted = Date.now();
  const threadParams: Record<string, unknown> = {
    model: config.model,
    cwd: config.workDir,
    approvalPolicy: "never",
    sandbox: "read-only",
    developerInstructions: prompt.developerInstructions,
    dynamicTools: [],
    experimentalRawEvents: false,
    persistExtendedHistory: false,
  };
  if (config.serviceTier) {
    threadParams.serviceTier = config.serviceTier;
  }
  const thread = await client.request<Record<string, unknown>>("thread/start", threadParams, timeoutMs);
  const threadStartMs = Date.now() - threadStarted;
  const threadId = readThreadId(thread);
  if (!threadId) {
    throw new RuntimeError(502, "codex_runtime_error", "thread_start_missing_thread_id", "thread_start");
  }

  const collector = new TerminalTurnCollector();
  const remove = client.addNotificationHandler((notification) => collector.handleNotification(notification));
  let turnId: string | undefined;
  let completed = false;
  try {
    const turnStarted = Date.now();
    const turnParams: Record<string, unknown> = {
      threadId,
      input: [{ type: "text", text: prompt.userText, text_elements: [] }],
      approvalPolicy: "never",
      sandboxPolicy: { type: "readOnly", access: { type: "fullAccess" }, networkAccess: false },
      dynamicTools: [],
      model: config.model,
    };
    if (config.serviceTier) {
      turnParams.serviceTier = config.serviceTier;
    }
    if (config.reasoningEffort) {
      turnParams.effort = config.reasoningEffort;
      turnParams.collaborationMode = {
        mode: "default",
        settings: {
          model: config.model,
          reasoning_effort: config.reasoningEffort,
          developer_instructions: null,
        },
      };
    }
    let turn: Record<string, unknown>;
    try {
      turn = await client.request<Record<string, unknown>>("turn/start", turnParams, timeoutMs);
    } catch (error) {
      if (classifyAuthFailure(error)) {
        throw new RuntimeError(401, "codex_login_failed", "codex_login_failed", "turn_start");
      }
      if (error instanceof RuntimeError && error.code === "codex_turn_timeout") {
        throw error;
      }
      throw new RuntimeError(502, "codex_model_error", "codex_turn_start_failed", "turn_start");
    }
    const turnStartMs = Date.now() - turnStarted;
    turnId = readTurnId(turn);
    if (!turnId) {
      throw new RuntimeError(502, "codex_runtime_error", "turn_start_missing_turn_id", "turn_start");
    }
    collector.setTurn(threadId, turnId);
    const terminalStarted = Date.now();
    const terminal = await collector.wait(timeoutMs);
    const terminalWaitMs = Date.now() - terminalStarted;
    completed = terminal.terminal;
    if (!terminal.terminal) {
      throw new RuntimeError(504, "codex_turn_timeout", "codex_turn_timeout", "turn_start");
    }
    if (terminal.error && classifyAuthFailure(terminal.error)) {
      throw new RuntimeError(401, "codex_login_failed", "codex_login_failed", "turn_start");
    }
    if (terminal.error) {
      throw new RuntimeError(502, "codex_model_error", "codex_model_error", "turn_start");
    }
    if (!terminal.assistantText) {
      throw new RuntimeError(502, "codex_invalid_output", "assistant_text_missing", "parse");
    }
    return {
      assistantText: terminal.assistantText,
      extractionPath: terminal.extractionPath,
      threadStartMs,
      turnStartMs,
      terminalWaitMs,
    };
  } finally {
    remove();
    await cleanup(client, threadId, turnId, !completed);
  }
}

async function cleanup(client: RpcClient, threadId: string, turnId: string | undefined, interrupt: boolean): Promise<void> {
  if (interrupt && turnId) {
    await client.request("turn/interrupt", { threadId, turnId }, 1000).catch(() => undefined);
  }
  await client.request("thread/unsubscribe", { threadId }, 1000).catch(() => undefined);
}

function readThreadId(result: Record<string, unknown>): string | undefined {
  const thread = result.thread;
  if (thread && typeof thread === "object" && !Array.isArray(thread)) {
    const value = (thread as Record<string, unknown>).id;
    return typeof value === "string" && value ? value : undefined;
  }
  return undefined;
}

function readTurnId(result: Record<string, unknown>): string | undefined {
  const turn = result.turn;
  if (turn && typeof turn === "object" && !Array.isArray(turn)) {
    const value = (turn as Record<string, unknown>).id;
    return typeof value === "string" && value ? value : undefined;
  }
  return undefined;
}
