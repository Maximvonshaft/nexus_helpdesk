import { createHash } from "node:crypto";
import type { Logger } from "pino";
import {
  DurableCallbackOutbox,
  type CallbackEnvelope,
  type CallbackKind
} from "./callbackOutbox.js";
import { connectorBinaryHeaders, connectorHeaders } from "./security.js";
import { assertSafeAccountId } from "./sessionStore.js";
import type {
  DesiredAccountResponse,
  NormalizedInboundMessage,
  SidecarConfig,
  WhatsAppMediaKind
} from "./types.js";

const CALLBACK_PATHS: Record<CallbackKind, string> = {
  inbound: "/api/integrations/whatsapp/baileys/inbound",
  status: "/api/integrations/whatsapp/baileys/status",
  delivery: "/api/integrations/whatsapp/baileys/delivery"
};

export class BackendClient {
  private readonly outbox: DurableCallbackOutbox;

  constructor(
    private readonly config: SidecarConfig,
    private readonly logger: Logger,
    outbox?: DurableCallbackOutbox
  ) {
    this.outbox = outbox ?? new DurableCallbackOutbox(
      config.callbackSpoolRoot,
      logger,
      config.connectorHmacSecret
    );
  }

  async postInbound(message: NormalizedInboundMessage): Promise<void> {
    this.outbox.enqueue({
      kind: "inbound",
      accountId: message.account_id,
      payload: message
    });
    await this.flushCallbacks();
  }

  async postMedia(options: {
    accountId: string;
    messageId: string;
    mediaKind: WhatsAppMediaKind;
    mediaType: string;
    filename?: string | null;
    content: Buffer;
  }): Promise<void> {
    const accountId = assertSafeAccountId(options.accountId);
    const messageId = String(options.messageId || "").trim();
    if (!messageId || messageId.length > 180) {
      throw new Error("invalid_media_message_id");
    }
    if (!options.content.byteLength) {
      throw new Error("empty_media_content");
    }
    const sha256 = createHash("sha256").update(options.content).digest("hex");
    const controller = new AbortController();
    const timeout = setTimeout(
      () => controller.abort(),
      Math.max(this.config.callbackTimeoutMs, 60_000)
    );
    try {
      const response = await fetch(
        `${this.config.backendUrl}/api/integrations/whatsapp/baileys/media`,
        {
          method: "POST",
          headers: connectorBinaryHeaders({
            accountId,
            connectorKey: this.config.connectorKey,
            hmacSecret: this.config.connectorHmacSecret,
            body: options.content,
            messageId,
            mediaKind: options.mediaKind,
            mediaType: options.mediaType,
            filename: options.filename,
            sha256
          }),
          body: options.content,
          signal: controller.signal
        }
      );
      if (!response.ok) {
        throw new Error(`backend_media_upload_failed:${response.status}`);
      }
    } finally {
      clearTimeout(timeout);
    }
  }

  async postStatus(accountId: string, payload: unknown): Promise<void> {
    const safeAccountId = assertSafeAccountId(accountId);
    this.outbox.enqueue({
      kind: "status",
      accountId: safeAccountId,
      payload,
      dedupeKey: `status:${safeAccountId}`
    });
    await this.flushCallbacks();
  }

  async postDelivery(accountId: string, payload: unknown): Promise<void> {
    const safeAccountId = assertSafeAccountId(accountId);
    const value = payload as {
      idempotency_key?: unknown;
      provider_message_id?: unknown;
      status?: unknown;
    };
    const identity =
      String(value.idempotency_key || "").trim() ||
      String(value.provider_message_id || "").trim() ||
      payloadIdentity(payload);
    this.outbox.enqueue({
      kind: "delivery",
      accountId: safeAccountId,
      payload,
      dedupeKey: `delivery:${safeAccountId}:${identity}:${String(value.status || "")}`
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
    const path = CALLBACK_PATHS[envelope.kind];
    const accountId = assertSafeAccountId(envelope.account_id);
    await this.postDirect(path, accountId, envelope.payload);
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

function payloadIdentity(payload: unknown): string {
  return createHash("sha256")
    .update(JSON.stringify(payload))
    .digest("hex");
}
