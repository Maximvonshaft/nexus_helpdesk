import {
  isObject,
  notificationMatchesTurn,
  notificationParams,
  notificationTurn,
  notificationWillRetry,
  type JsonObject,
  type RpcNotification,
} from "./notification-correlation.js";

export type TerminalTurnResult = {
  terminal: boolean;
  status?: string;
  error?: unknown;
  assistantText?: string;
  extractionPath?: string;
  turn?: JsonObject;
  notificationsObserved: number;
  timedOut?: boolean;
};

export class TerminalTurnCollector {
  private threadId?: string;
  private turnId?: string;
  private readonly early: RpcNotification[] = [];
  private readonly matched: RpcNotification[] = [];
  private latestTurn?: JsonObject;
  private deltaText = "";
  private assistantText?: string;
  private extractionPath?: string;
  private terminalError?: unknown;
  private resolved?: TerminalTurnResult;
  private resolveWait?: (value: TerminalTurnResult) => void;

  handleNotification(notification: RpcNotification): void {
    if (!this.threadId) {
      this.early.push(notification);
      this.trim(this.early, 200);
      return;
    }
    this.process(notification);
  }

  setTurn(threadId: string, turnId?: string): void {
    this.threadId = threadId;
    this.turnId = turnId;
    const buffered = this.early.splice(0);
    for (const notification of buffered) {
      this.process(notification);
    }
  }

  wait(timeoutMs: number): Promise<TerminalTurnResult> {
    if (this.resolved) {
      return Promise.resolve(this.resolved);
    }
    return new Promise((resolve) => {
      this.resolveWait = resolve;
      setTimeout(() => {
        if (!this.resolved) {
          this.finish({
            terminal: false,
            timedOut: true,
            status: this.readStatus(),
            error: this.terminalError,
            assistantText: this.readAssistantText(),
            extractionPath: this.extractionPath,
            turn: this.latestTurn,
            notificationsObserved: this.matched.length,
          });
        }
      }, timeoutMs);
    });
  }

  snapshot(): TerminalTurnResult {
    return {
      terminal: Boolean(this.resolved?.terminal),
      status: this.readStatus(),
      error: this.terminalError,
      assistantText: this.readAssistantText(),
      extractionPath: this.extractionPath,
      turn: this.latestTurn,
      notificationsObserved: this.matched.length,
    };
  }

  private process(notification: RpcNotification): void {
    if (!this.threadId || !notificationMatchesTurn(notification, { threadId: this.threadId, turnId: this.turnId })) {
      return;
    }
    this.matched.push(notification);
    this.trim(this.matched, 300);
    const method = notification.method;
    const turn = notificationTurn(notification);
    if (turn) {
      this.latestTurn = turn;
      this.captureAssistantFromTurn(turn, `notification:${method}.turn.items[type=agentMessage].text`);
    }
    if (method === "item/agentMessage/delta") {
      const delta = notificationParams(notification).delta;
      if (typeof delta === "string" && delta) {
        this.deltaText += delta;
        this.extractionPath = "notification:item/agentMessage/delta.params.delta";
      }
    }
    if (method === "turn/completed") {
      this.finish({
        terminal: true,
        status: this.readStatus() || "completed",
        error: this.terminalError,
        assistantText: this.readAssistantText(),
        extractionPath: this.extractionPath,
        turn: this.latestTurn,
        notificationsObserved: this.matched.length,
      });
    }
    if (method === "error" && !notificationWillRetry(notification)) {
      const params = notificationParams(notification);
      this.terminalError = params.error || params;
      this.finish({
        terminal: true,
        status: "error",
        error: this.terminalError,
        assistantText: this.readAssistantText(),
        extractionPath: this.extractionPath,
        turn: this.latestTurn,
        notificationsObserved: this.matched.length,
      });
    }
  }

  private captureAssistantFromTurn(turn: JsonObject, path: string): void {
    const items = Array.isArray(turn.items) ? turn.items : [];
    const texts: string[] = [];
    for (const item of items) {
      if (isObject(item) && item.type === "agentMessage" && typeof item.text === "string" && item.text.trim()) {
        texts.push(item.text.trim());
      }
    }
    if (texts.length > 0) {
      this.assistantText = texts.join("\n").trim();
      this.extractionPath = path;
    }
    if (isObject(turn.error)) {
      this.terminalError = turn.error;
    }
  }

  private readAssistantText(): string | undefined {
    return this.assistantText || (this.deltaText.trim() ? this.deltaText.trim() : undefined);
  }

  private readStatus(): string | undefined {
    const value = this.latestTurn?.status;
    return typeof value === "string" ? value : undefined;
  }

  private finish(result: TerminalTurnResult): void {
    if (this.resolved) {
      return;
    }
    this.resolved = result;
    this.resolveWait?.(result);
  }

  private trim<T>(items: T[], max: number): void {
    if (items.length > max) {
      items.splice(0, items.length - max);
    }
  }
}
