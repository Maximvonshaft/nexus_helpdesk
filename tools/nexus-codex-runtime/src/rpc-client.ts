import { createInterface, type Interface } from "node:readline";
import type { ChildProcessWithoutNullStreams } from "node:child_process";
import { RuntimeError } from "./errors.js";
import { redactString } from "./redaction.js";
import type { RpcNotification } from "./notification-correlation.js";

export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

type PendingRequest = {
  method: string;
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  timer: NodeJS.Timeout;
};

export class RpcClient {
  private readonly process: ChildProcessWithoutNullStreams;
  private readonly lines: Interface;
  private readonly pending = new Map<number, PendingRequest>();
  private readonly notificationHandlers = new Set<(notification: RpcNotification) => void>();
  private nextId = 1;
  private closed = false;
  private stderrTail = "";

  constructor(child: ChildProcessWithoutNullStreams) {
    this.process = child;
    this.lines = createInterface({ input: child.stdout });
    this.lines.on("line", (line) => this.handleLine(line));
    child.stderr.on("data", (chunk: Buffer | string) => {
      this.stderrTail = (this.stderrTail + chunk.toString("utf8")).slice(-2000);
    });
    child.once("error", (error) => {
      this.closeWithError(
        new RuntimeError(503, "codex_appserver_start_failed", `codex app-server error: ${error.message}`),
      );
    });
    child.once("exit", (code, signal) => {
      this.closeWithError(new RuntimeError(
        503,
        "codex_appserver_start_failed",
        `codex app-server exited code=${code ?? "null"} signal=${signal ?? "null"} stderr=${redactString(this.stderrTail)}`,
      ));
    });
  }

  async request<T = unknown>(method: string, params?: unknown, timeoutMs = 8000): Promise<T> {
    if (this.closed) {
      throw new RuntimeError(503, "codex_appserver_start_failed", "codex_appserver_closed");
    }
    const id = this.nextId++;
    const payload = JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n";
    return await new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new RuntimeError(504, method === "turn/start" ? "codex_turn_timeout" : "codex_runtime_error", `${method}_timeout`));
      }, timeoutMs);
      this.pending.set(id, { method, resolve: resolve as (value: unknown) => void, reject, timer });
      this.process.stdin.write(payload, "utf8", (error) => {
        if (error) {
          clearTimeout(timer);
          this.pending.delete(id);
          reject(new RuntimeError(503, "codex_appserver_start_failed", error.message));
        }
      });
    });
  }

  addNotificationHandler(handler: (notification: RpcNotification) => void): () => void {
    this.notificationHandlers.add(handler);
    return () => this.notificationHandlers.delete(handler);
  }

  close(): void {
    this.closed = true;
    this.lines.close();
    this.process.kill("SIGTERM");
  }

  private handleLine(line: string): void {
    let decoded: unknown;
    try {
      decoded = JSON.parse(line);
    } catch {
      return;
    }
    if (!decoded || typeof decoded !== "object") {
      return;
    }
    const message = decoded as Record<string, unknown>;
    if (typeof message.id === "number" && this.pending.has(message.id)) {
      const pending = this.pending.get(message.id)!;
      this.pending.delete(message.id);
      clearTimeout(pending.timer);
      if (message.error) {
        pending.reject(new RuntimeError(502, "codex_runtime_error", `${pending.method}: ${JSON.stringify(message.error)}`));
      } else {
        pending.resolve(message.result);
      }
      return;
    }
    if (message.method && message.id !== undefined) {
      this.respondToServerRequest(message);
      return;
    }
    if (typeof message.method === "string") {
      const notification = { method: message.method, params: message.params } satisfies RpcNotification;
      for (const handler of this.notificationHandlers) {
        handler(notification);
      }
    }
  }

  private respondToServerRequest(message: Record<string, unknown>): void {
    const method = typeof message.method === "string" ? message.method : "";
    const id = message.id;
    let response: Record<string, unknown>;
    if (method === "account/chatgptAuthTokens/refresh") {
      response = { id, error: { message: "request-scoped token refresh is unavailable" } };
    } else if (method.endsWith("/requestApproval")) {
      response = { id, result: { decision: "decline" } };
    } else if (method === "item/permissions/requestApproval") {
      response = { id, result: { permissions: {}, scope: "turn" } };
    } else if (method === "item/tool/requestUserInput") {
      response = { id, result: { answers: {} } };
    } else {
      response = { id, result: {} };
    }
    this.process.stdin.write(JSON.stringify(response) + "\n");
  }

  private closeWithError(error: Error): void {
    this.closed = true;
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }
}
