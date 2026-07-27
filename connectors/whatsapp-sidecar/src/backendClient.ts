import type { Logger } from "pino";
import { DurableCallbackOutbox, type CallbackEnvelope } from "./callbackOutbox.js";
import { connectorHeaders } from "./security.js";
import type {
  DesiredAccountResponse,
  NormalizedInboundMessage,
  SidecarConfig
} from "./types.js";

export class BackendClient {
  private readonly outbox: DurableCallbackOutbox;

  constructor(
    private readonly config: SidecarConfig,
    private readonly logger: Logger,
    outbox?: DurableCallbackOutbox
  ) {
    this.outbox = outbox ?? new DurableCallbackOutbox(config.callbackSpoolRoot, logger);
  }

  async postInbound(message: NormalizedInboundMessage): Promise<void> {
    this.outbox.enqueue({
      path: "/api/integrations/whatsapp/baileys/inbound",
      accountId: message.account_id,
      payload: message,
      dedupeKey: undefined
    });
    await this.flushCallbacks();
  }

  async postStatus(accountId: string, payload: unknown): Promise<void> {
    this.outbox.enqueue({
      path: "/api/integrations/whatsapp/baileys/status",
      accountId,
      payload,
      dedupeKey: `status:${accountId}`
    });
    await this.flushCallbacks();
  }

  async postDelivery(accountId: string, payload: unknown): Promise<void> {
    const value = payload as {
      idempotency_key?: unknown;
      provider_message_id?: unknown;
      status?: unknown;
    };
    const identity =
      String(value.idempotency_key || "").trim() ||
      String(value.provider_message_id || "").trim() ||
      cryptoSafePayloadIdentity(payload);
    this.outbox.enqueue({
      path: "/api/integrations/whatsapp/baileys/delivery",
      accountId,
      payload,
      dedupeKey: `delivery:${accountId}:${identity}:${String(value.status || "")}`
    });
    await this.flushCallbacks();
  }

  async fetchDesiredAccounts(): Promise<DesiredAccountResponse> {
    const payload = { purpose: "desired_state_reconciliation" };
    const response = await this.postDirect(
      "/api/integrations/whatsapp/baileys/desired-state",
      "reconciler",
      payload
    );
    if (
      response.ok !== true ||
      !Array.isArray((response as DesiredAccountResponse).accounts)
    ) {
      throw new Error("invalid_desired_account_response");
    }
    return response as DesiredAccountResponse;
  }

  async flushCallbacks(): Promise<{ delivered: number; pending: number }> {
    return await this.outbox.drain(async (envelope) => {
      await this.sendEnvelope(envelope);
    });
  }

  pendingCallbacks(): number {
    return this.outbox.count();
  }

  private async sendEnvelope(envelope: CallbackEnvelope): Promise<void> {
    await this.postDirect(envelope.path, envelope.account_id, envelope.payload);
  }

  private async postDirect(
    path: string,
    accountId: string,
    payload: unknown
  ): Promise<Record<string, unknown>> {
    const rawBody = JSON.stringify(payload);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.config.callbackTimeoutMs);
    try {
      const response = await fetch(`${this.config.backendUrl}${path}`, {
        method: "POST",
        headers: connectorHeaders({
          accountId,
          connectorKey: this.config.connectorKey,
          hmacSecret: this.config.connectorHmacSecret,
          rawBody
        }),
        body: rawBody,
        signal: controller.signal
      });
      if (!response.ok) {
        throw new Error(`backend_callback_failed:${response.status}`);
      }
      const data = await response.json().catch(() => ({}));
      return data && typeof data === "object" && !Array.isArray(data)
        ? (data as Record<string, unknown>)
        : {};
    } finally {
      clearTimeout(timeout);
    }
  }
}

function cryptoSafePayloadIdentity(payload: unknown): string {
  const serialized = JSON.stringify(payload);
  let hash = 2166136261;
  for (let index = 0; index < serialized.length; index += 1) {
    hash ^= serialized.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}
