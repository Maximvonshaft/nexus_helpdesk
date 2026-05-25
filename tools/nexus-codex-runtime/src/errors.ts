import { redactString } from "./redaction.js";

export class RuntimeError extends Error {
  readonly status: number;
  readonly code: string;
  readonly stage?: string;

  constructor(status: number, code: string, message?: string, stage?: string) {
    super(redactString(message || code));
    this.name = "RuntimeError";
    this.status = status;
    this.code = code;
    this.stage = stage;
  }
}

export function normalizeError(error: unknown): RuntimeError {
  if (error instanceof RuntimeError) {
    return error;
  }
  if (error instanceof Error) {
    return new RuntimeError(502, "codex_runtime_error", error.message);
  }
  return new RuntimeError(502, "codex_runtime_error", String(error));
}

export function classifyAuthFailure(error: unknown): boolean {
  const text = (error instanceof Error ? error.message : JSON.stringify(error)).toLowerCase();
  return ["auth", "unauthorized", "401", "403", "login", "relogin", "sign in", "signin"].some((term) =>
    text.includes(term),
  );
}
