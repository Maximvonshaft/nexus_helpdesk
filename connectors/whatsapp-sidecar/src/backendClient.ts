import { createHash } from "node:crypto";
import { join } from "node:path";
import type { Logger } from "pino";
import {
  DurableCallbackOutbox,
  type CallbackEnvelope,
  type CallbackKind
} from "./callbackOutbox.js";
import {
  DurableMediaOutbox,
  type InboundMediaEnvelope
} from "./mediaOutbox.js";
import { connectorBinaryHeaders, connectorHeaders } from "./security.js";
import { assertSafeAccountId } from "./sessionStore.js";
import type {
  DesiredAccount,
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
const MEDIA_PATH = "/api/integrations/whatsapp/baileys/media";

export class BackendClient {
  private readonly outbox: DurableCallbackOutbox;
  private readonly mediaOutbox: DurableMediaOutbox;

  constructor(
    private readonly config: SidecarConfig,
    private readonly logger: Logger,
    outbox?: DurableCallbackOutbox,
    mediaOutbox?: DurableMediaOutbox
  ) {
    this.outbox = outbox ?? new DurableCallbackOutbox(
      config.callbackSpoolRoot,
      logger,
      config.connectorHmacSecret
    );
    this.mediaOutbox = mediaOutbox ?? new DurableMediaOutbox(
      join(config.callbackSpoolRoot, "media"),
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
    this.mediaOutbox.enqueue({
      accountId: options.accountId,
      externalMessageId: options.messageId,
      mediaKind: options.mediaKind,
      mediaType: options.mediaType,
      fileName: options.filename,
      content: options.content
    });
    await this.flushCallbacks();
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
    return parseDesiredAccountResponse(response);
  }

  async flushCallbacks(): Promise<{ delivered: number; pending: number }> {
    const callbacks = await this.outbox.drain(async (envelope) => {
      await this.sendEnvelope(envelope);
    });
    const media = await this.mediaOutbox.drain(async (envelope) => {
      await this.sendMediaEnvelope(envelope);
    });
    return {
      delivered: callbacks.delivered + media.delivered,
      pending: callbacks.pending + media.pending
    };
  }

  pendingCallbacks(): number {
    return this.outbox.count() + this.mediaOutbox.count();
  }

  private async sendEnvelope(envelope: CallbackEnvelope): Promise<void> {
    const path = CALLBACK_PATHS[envelope.kind];
    const accountId = assertSafeAccountId(envelope.account_id);
    await this.postDirect(path, accountId, envelope.payload);
  }

  private async sendMediaEnvelope(envelope: InboundMediaEnvelope): Promise<void> {
    const controller = new AbortController();
    const timeout = setTimeout(
      () => controller.abort(),
      Math.max(this.config.callbackTimeoutMs, 60_000)
    );
    try {
      const response = await fetch(`${this.config.backendUrl}${MEDIA_PATH}`, {
        method: "POST",
        headers: connectorBinaryHeaders({
          accountId: envelope.account_id,
          connectorKey: this.config.connectorKey,
          hmacSecret: this.config.connectorHmacSecret,
          body: envelope.content,
          messageId: envelope.external_message_id,
          mediaKind: envelope.media_kind,
          mediaType: envelope.media_type,
          filename: envelope.file_name,
          sha256: envelope.sha256
        }),
        body: Uint8Array.from(envelope.content),
        signal: controller.signal
      });
      if (!response.ok) {
        throw new Error(`backend_media_upload_failed:${response.status}`);
      }
    } finally {
      clearTimeout(timeout);
    }
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

function parseDesiredAccountResponse(
  response: Record<string, unknown>
): DesiredAccountResponse {
  if (response.ok !== true || !Array.isArray(response.accounts)) {
    throw new Error("invalid_desired_account_response");
  }
  const accounts: DesiredAccount[] = response.accounts.map((candidate) => {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) {
      throw new Error("invalid_desired_account_response");
    }
    const value = candidate as Record<string, unknown>;
    const accountId = assertSafeAccountId(String(value.account_id || ""));
    const generation = Number(value.generation);
    if (!Number.isSafeInteger(generation) || generation < 0) {
      throw new Error("invalid_desired_account_response");
    }
    return {
      account_id: accountId,
      generation
    };
  });
  return { ok: true, accounts };
}

function payloadIdentity(payload: unknown): string {
  return createHash("sha256")
    .update(JSON.stringify(payload))
    .digest("hex");
}
