export type JsonObject = Record<string, unknown>;

export type RpcNotification = {
  method: string;
  params?: unknown;
};

export function notificationParams(notification: RpcNotification): JsonObject {
  return isObject(notification.params) ? notification.params : {};
}

export function notificationThreadId(notification: RpcNotification): string | undefined {
  const params = notificationParams(notification);
  const turn = isObject(params.turn) ? params.turn : undefined;
  return readString(turn, "threadId") || readString(params, "threadId");
}

export function notificationTurnId(notification: RpcNotification): string | undefined {
  const params = notificationParams(notification);
  const turn = isObject(params.turn) ? params.turn : undefined;
  return readString(turn, "id") || readString(params, "turnId");
}

export function notificationTurn(notification: RpcNotification): JsonObject | undefined {
  const params = notificationParams(notification);
  return isObject(params.turn) ? params.turn : undefined;
}

export function notificationMatchesTurn(
  notification: RpcNotification,
  active: { threadId: string; turnId?: string },
): boolean {
  const threadId = notificationThreadId(notification);
  const turnId = notificationTurnId(notification);
  if (threadId && threadId !== active.threadId) {
    return false;
  }
  if (active.turnId && turnId && turnId !== active.turnId) {
    return false;
  }
  return threadId === active.threadId || Boolean(active.turnId && turnId === active.turnId);
}

export function notificationWillRetry(notification: RpcNotification): boolean {
  const params = notificationParams(notification);
  if (typeof params.willRetry === "boolean") {
    return params.willRetry;
  }
  const error = isObject(params.error) ? params.error : undefined;
  return typeof error?.willRetry === "boolean" ? error.willRetry : false;
}

export function isObject(value: unknown): value is JsonObject {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

export function readString(record: JsonObject | undefined, key: string): string | undefined {
  const value = record?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}
