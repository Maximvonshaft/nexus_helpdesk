import { RuntimeError } from "./errors.js";

export function nowMs(): number {
  return Date.now();
}

export function deadlineFromHeader(value: string | string[] | undefined, defaultTimeoutMs: number): number {
  const raw = Array.isArray(value) ? value[0] : value;
  if (!raw) {
    return nowMs() + defaultTimeoutMs;
  }
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new RuntimeError(504, "codex_turn_timeout", "invalid_deadline", "deadline");
  }
  if (parsed <= nowMs()) {
    throw new RuntimeError(504, "codex_turn_timeout", "deadline_exceeded", "deadline");
  }
  return parsed;
}

export function remainingMs(deadlineMs: number, floorMs = 1): number {
  return Math.max(floorMs, deadlineMs - nowMs());
}

export async function withTimeout<T>(
  promise: Promise<T>,
  timeoutMs: number,
  code: string,
  stage: string,
): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<T>((_resolve, reject) => {
        timer = setTimeout(() => reject(new RuntimeError(504, code, `${stage}_timeout`, stage)), timeoutMs);
      }),
    ]);
  } finally {
    if (timer) {
      clearTimeout(timer);
    }
  }
}
