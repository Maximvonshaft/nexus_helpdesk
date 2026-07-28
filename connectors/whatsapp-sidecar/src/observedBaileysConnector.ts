import type { Logger } from "pino";
import { BaileysConnector } from "./baileysClient.js";
import { SessionStore } from "./sessionStore.js";
import type {
  AccountSnapshot,
  NormalizedInboundMessage,
  SidecarConfig,
  WhatsAppMediaKind
} from "./types.js";

type InboundHandler = (message: NormalizedInboundMessage) => Promise<void>;
type StatusHandler = (accountId: string, snapshot: AccountSnapshot) => Promise<void>;
type MediaHandler = (options: {
  accountId: string;
  messageId: string;
  mediaKind: WhatsAppMediaKind;
  mediaType: string;
  filename?: string | null;
  content: Buffer;
}) => Promise<void>;
type DeliveryHandler = (
  accountId: string,
  payload: Record<string, unknown>
) => Promise<void>;

type EventSocket = {
  ev: {
    on(event: string, handler: (payload: any) => void): void;
  };
};
type RuntimeAccountHost = {
  accounts: Map<string, { socket?: EventSocket }>;
};

export type BaileysDeliveryStatus = "sent" | "delivered" | "read";

function numericStatus(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function deliveryStatusFromMessageUpdate(
  candidate: unknown
): BaileysDeliveryStatus | null {
  const value = candidate as { update?: { status?: unknown }; status?: unknown };
  const status = numericStatus(value?.update?.status ?? value?.status);
  if (status === null) return null;
  if (status >= 4) return "read";
  if (status >= 3) return "delivered";
  if (status >= 2) return "sent";
  return null;
}

export function deliveryStatusFromReceipt(
  candidate: unknown
): BaileysDeliveryStatus | null {
  const receipt = (candidate as { receipt?: Record<string, unknown> })?.receipt;
  if (!receipt) return null;
  if (receipt.playedTimestamp || receipt.readTimestamp) return "read";
  if (
    receipt.receiptTimestamp ||
    receipt.deliveredTimestamp ||
    receipt.deliveryTimestamp
  ) {
    return "delivered";
  }
  return null;
}

function providerMessageId(candidate: unknown): string | null {
  const value = candidate as { key?: { id?: unknown } };
  const id = String(value?.key?.id || "").trim();
  return id ? id.slice(0, 180) : null;
}

function timestampValue(value: unknown): number | null {
  if (value instanceof Date) return value.getTime();
  if (typeof value === "bigint") return Number(value);
  if (
    value &&
    typeof value === "object" &&
    typeof (value as { toNumber?: unknown }).toNumber === "function"
  ) {
    return Number((value as { toNumber: () => number }).toNumber());
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function receiptOccurredAt(candidate: unknown): string {
  const value = candidate as {
    receipt?: Record<string, unknown>;
    update?: Record<string, unknown>;
  };
  const receipt = value?.receipt || {};
  const update = value?.update || {};
  const raw =
    receipt.playedTimestamp ||
    receipt.readTimestamp ||
    receipt.receiptTimestamp ||
    receipt.deliveredTimestamp ||
    receipt.deliveryTimestamp ||
    update.messageTimestamp ||
    update.timestamp;
  const parsed = timestampValue(raw);
  if (parsed === null) return new Date().toISOString();
  const milliseconds = parsed > 10_000_000_000 ? parsed : parsed * 1000;
  return new Date(milliseconds).toISOString();
}

function participant(candidate: unknown): string | null {
  const receipt = (candidate as { receipt?: { userJid?: unknown } })?.receipt;
  const value = String(receipt?.userJid || "").trim();
  return value ? value.slice(0, 180) : null;
}

export class ObservedBaileysConnector extends BaileysConnector {
  private readonly installedSockets = new WeakSet<object>();

  constructor(
    sessions: SessionStore,
    logger: Logger,
    onInbound: InboundHandler,
    onMedia: MediaHandler,
    private readonly observedStatus: StatusHandler,
    config: SidecarConfig,
    private readonly onDelivery: DeliveryHandler
  ) {
    super(sessions, logger, onInbound, onMedia, observedStatus, config);
  }

  override async start(
    accountId: string,
    generation?: number
  ): Promise<AccountSnapshot> {
    const snapshot = await super.start(accountId, generation);
    this.installReceiptListeners(accountId);
    // Super emits every transition except its active-socket fast path. Posting the
    // returned snapshot here keeps observed_generation convergent in both paths;
    // BackendClient coalesces status callbacks by account identity.
    await this.observedStatus(accountId, snapshot);
    return snapshot;
  }

  private runtimeSocket(accountId: string): EventSocket | null {
    const host = this as unknown as RuntimeAccountHost;
    return host.accounts.get(accountId)?.socket || null;
  }

  private installReceiptListeners(accountId: string): void {
    const socket = this.runtimeSocket(accountId);
    if (!socket || this.installedSockets.has(socket as object)) return;
    this.installedSockets.add(socket as object);
    socket.ev.on("messages.update", (updates: unknown[]) => {
      void this.forwardMessageUpdates(accountId, updates || []);
    });
    socket.ev.on("message-receipt.update", (updates: unknown[]) => {
      void this.forwardReceiptUpdates(accountId, updates || []);
    });
  }

  async forwardMessageUpdates(
    accountId: string,
    updates: unknown[]
  ): Promise<void> {
    for (const candidate of updates) {
      const value = candidate as { key?: { fromMe?: unknown } };
      if (value?.key?.fromMe !== true) continue;
      const id = providerMessageId(candidate);
      const status = deliveryStatusFromMessageUpdate(candidate);
      if (!id || !status) continue;
      const occurredAt = receiptOccurredAt(candidate);
      await this.forwardDelivery(accountId, {
        account_id: accountId,
        idempotency_key: `baileys-message-update:${id}:${status}`,
        provider_message_id: id,
        status,
        occurred_at: occurredAt,
        metadata: { source_event: "messages.update" }
      });
    }
  }

  async forwardReceiptUpdates(
    accountId: string,
    updates: unknown[]
  ): Promise<void> {
    for (const candidate of updates) {
      const id = providerMessageId(candidate);
      const status = deliveryStatusFromReceipt(candidate);
      if (!id || !status) continue;
      const occurredAt = receiptOccurredAt(candidate);
      await this.forwardDelivery(accountId, {
        account_id: accountId,
        idempotency_key: `baileys-message-receipt:${id}:${status}:${occurredAt}`,
        provider_message_id: id,
        status,
        occurred_at: occurredAt,
        metadata: {
          source_event: "message-receipt.update",
          participant: participant(candidate)
        }
      });
    }
  }

  private async forwardDelivery(
    accountId: string,
    payload: Record<string, unknown>
  ): Promise<void> {
    try {
      await this.onDelivery(accountId, payload);
    } catch (error) {
      // BackendClient enqueues before attempting the network flush, so an error
      // here means durable retry remains pending rather than losing the receipt.
      const logger = (this as unknown as { logger: Logger }).logger;
      logger.warn(
        {
          account_id: accountId,
          provider_message_id: payload.provider_message_id,
          status: payload.status,
          error_name: error instanceof Error ? error.name : "UnknownError"
        },
        "whatsapp_delivery_receipt_callback_pending"
      );
    }
  }
}
