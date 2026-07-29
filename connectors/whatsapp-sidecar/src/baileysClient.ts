import { join } from "node:path";
import type { Logger } from "pino";
import { Boom } from "@hapi/boom";
import makeWASocket, {
  Browsers,
  DisconnectReason,
  downloadMediaMessage,
  fetchLatestBaileysVersion,
  useMultiFileAuthState,
  type WASocket
} from "@whiskeysockets/baileys";
import { normalizeBaileysInbound } from "./inboundMapper.js";
import {
  DurableMediaDownloadOutbox,
  type MediaDownloadEnvelope
} from "./mediaDownloadOutbox.js";
import { qrDataUrl } from "./qrManager.js";
import type {
  AccountSnapshot,
  NormalizedInboundMessage,
  PairingCodeRequest,
  PairingCodeResult,
  SendMediaRequest,
  SendRequest,
  SendResult,
  SidecarConfig,
  WhatsAppConnector,
  WhatsAppMediaKind
} from "./types.js";
import { SessionStore, type AccountOwnerLease } from "./sessionStore.js";


type InboundHandler = (message: NormalizedInboundMessage) => Promise<void>;
type StatusHandler = (accountId: string, snapshot: AccountSnapshot) => Promise<void>;
type DeliveryHandler = (accountId: string, payload: Record<string, unknown>) => Promise<void>;
type MediaHandler = (options: {
  accountId: string;
  messageId: string;
  mediaKind: WhatsAppMediaKind;
  mediaType: string;
  filename?: string | null;
  content: Buffer;
}) => Promise<void>;
type RecipientDeliveryStatus = "delivered" | "read";

const PAIRING_CODE_ATTEMPTS = 5;
const PAIRING_CODE_READY_DELAY_MS = 1500;
const PAIRING_CODE_RETRY_DELAY_MS = 2000;
const PAIRING_CODE_WINDOW_MS = 180_000;
const MEDIA_LIMITS: Record<WhatsAppMediaKind, number> = {
  image: 5 * 1024 * 1024,
  audio: 16 * 1024 * 1024,
  video: 16 * 1024 * 1024,
  document: 100 * 1024 * 1024,
  sticker: 500 * 1024
};

interface RuntimeAccount {
  accountId: string;
  generation: number;
  socket?: WASocket;
  suppressReconnectFor?: WASocket;
  owner?: AccountOwnerLease;
  pairingUntilMs?: number;
  qrTimer?: ReturnType<typeof setTimeout>;
  reconnectTimer?: ReturnType<typeof setTimeout>;
  stopped: boolean;
  reconnectAttempts: number;
  status: AccountSnapshot;
}

function baseSnapshot(
  accountId: string,
  generation = 0,
  linked = false
): AccountSnapshot {
  return {
    account_id: accountId,
    status: "idle",
    authentication_state: linked ? "linked" : "unconfigured",
    listener_state: "stopped",
    qr_status: "none",
    generation,
    reconnect_count: 0
  };
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, ms));
}

function errorStatusCode(error: unknown): number | undefined {
  const outputStatus = (error as { output?: { statusCode?: unknown } })?.output?.statusCode;
  if (typeof outputStatus === "number") return outputStatus;
  const statusCode = (error as { statusCode?: unknown })?.statusCode;
  return typeof statusCode === "number" ? statusCode : undefined;
}

function errorCode(error: unknown, fallback: string): string {
  const payloadError = (error as { output?: { payload?: { error?: unknown } } })?.output?.payload?.error;
  if (typeof payloadError === "string" && payloadError.trim()) return payloadError.slice(0, 80);
  const code = (error as { code?: unknown })?.code;
  if (typeof code === "string" && code.trim()) return code.slice(0, 80);
  const statusCode = errorStatusCode(error);
  return statusCode ? `http_${statusCode}` : fallback;
}

function isRetryablePairingError(error: unknown): boolean {
  const statusCode = errorStatusCode(error);
  if (statusCode && [408, 425, 428, 429].includes(statusCode)) return true;
  if (statusCode && statusCode >= 500) return true;
  const message = (error as { message?: unknown })?.message;
  if (typeof message !== "string") return false;
  const normalized = message.toLowerCase();
  return normalized.includes("connection closed") || normalized.includes("timed out") || normalized.includes("not open");
}

function mediaDownloadError(message: string, retryable: boolean): Error & { retryable: boolean } {
  return Object.assign(new Error(message), { retryable });
}

function timestampNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "bigint") return Number(value);
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  const candidate = value as { toNumber?: () => number; low?: number } | null | undefined;
  if (candidate && typeof candidate.toNumber === "function") {
    const parsed = candidate.toNumber();
    return Number.isFinite(parsed) ? parsed : null;
  }
  if (candidate && typeof candidate.low === "number") return candidate.low;
  return null;
}

function providerTimestampIso(value: unknown): string {
  const numeric = timestampNumber(value);
  if (numeric === null || numeric <= 0) return new Date().toISOString();
  const milliseconds = numeric < 1_000_000_000_000 ? numeric * 1000 : numeric;
  const date = new Date(milliseconds);
  return Number.isNaN(date.getTime()) ? new Date().toISOString() : date.toISOString();
}

export function deliveryStatusFromMessageUpdate(update: any): RecipientDeliveryStatus | null {
  const raw = update?.status;
  if (typeof raw === "string") {
    const normalized = raw.trim().toLowerCase();
    if (normalized.includes("read") || normalized.includes("played")) return "read";
    if (normalized.includes("deliver")) return "delivered";
  }
  const numeric = Number(raw);
  if (!Number.isFinite(numeric)) return null;
  if (numeric >= 4) return "read";
  if (numeric === 3) return "delivered";
  return null;
}

export function deliveryStatusFromReceiptUpdate(receipt: any): RecipientDeliveryStatus | null {
  if (receipt?.playedTimestamp || receipt?.readTimestamp) return "read";
  if (receipt?.receiptTimestamp || receipt?.deliveredTimestamp) return "delivered";
  return null;
}

export class BaileysConnector implements WhatsAppConnector {
  private readonly accounts = new Map<string, RuntimeAccount>();
  private readonly mediaDownloads: DurableMediaDownloadOutbox;
  private readonly mediaDrainAccounts = new Set<string>();
  private readonly mediaRetryTimer: ReturnType<typeof setInterval>;

  constructor(
    private readonly sessions: SessionStore,
    private readonly logger: Logger,
    private readonly onInbound: InboundHandler,
    private readonly onMedia: MediaHandler,
    private readonly onStatus: StatusHandler,
    private readonly config: SidecarConfig,
    private readonly onDelivery: DeliveryHandler = async () => undefined
  ) {
    this.mediaDownloads = new DurableMediaDownloadOutbox(
      join(config.callbackSpoolRoot, "media-downloads"),
      logger,
      config.connectorHmacSecret
    );
    this.mediaRetryTimer = setInterval(() => {
      void this.drainAllMediaDownloads();
    }, config.callbackRetryIntervalMs);
    this.mediaRetryTimer.unref?.();
  }

  async start(accountId: string, generation?: number): Promise<AccountSnapshot> {
    const account = this.account(accountId);
    const previousGeneration = account.generation;
    if (generation !== undefined) account.generation = Math.max(0, generation);
    account.status.generation = account.generation;
    account.stopped = false;
    if (
      account.socket &&
      ["connected", "connecting", "qr_pending", "auth_persisting"].includes(account.status.status)
    ) {
      if (account.generation !== previousGeneration) await this.emitStatus(account);
      return account.status;
    }
    this.clearReconnectTimer(account);
    if (!account.owner) {
      try {
        account.owner = this.sessions.acquireOwner(accountId);
      } catch (error) {
        account.status = {
          ...account.status,
          status: "error",
          authentication_state: this.sessions.credentialsPersisted(accountId) ? "linked" : "unconfigured",
          listener_state: "error",
          generation: account.generation,
          last_error_code: "whatsapp_connection_owner_busy",
          last_error_message: error instanceof Error ? error.message : String(error)
        };
        await this.emitStatus(account);
        return account.status;
      }
    }

    account.status = {
      ...account.status,
      status: "connecting",
      authentication_state: this.sessions.credentialsPersisted(accountId) ? "linked" : "pending",
      listener_state: "starting",
      generation: account.generation,
      qr_status: "none",
      qr: null,
      qr_data_url: null,
      qr_expires_at: null,
      last_error_code: null,
      last_error_message: null,
      last_transport_activity_at: new Date().toISOString()
    };
    await this.emitStatus(account);

    try {
      const { state, saveCreds } = await useMultiFileAuthState(this.sessions.accountPath(accountId));
      const { version } = await fetchLatestBaileysVersion();
      const socket = makeWASocket({
        version,
        auth: state,
        browser: Browsers.ubuntu(this.config.browserName),
        printQRInTerminal: false,
        syncFullHistory: false,
        markOnlineOnConnect: false,
        logger: this.logger.child({ account_id: accountId }) as any
      });
      account.socket = socket;
      socket.ev.on("creds.update", () => {
        void Promise.resolve(saveCreds()).catch((error) => {
          this.abortForCredentialFailure(account, socket, error);
        });
      });
      socket.ev.on("connection.update", (update) => {
        void this.handleConnectionUpdate(account, socket, update).catch((error) => {
          this.logger.error({ account_id: accountId, error }, "whatsapp_connection_update_failed");
        });
      });
      socket.ev.on("messages.upsert", ({ messages }) => {
        void this.handleMessages(account, messages || []).catch((error) => {
          this.logger.error({ account_id: accountId, error }, "whatsapp_messages_upsert_failed");
        });
      });
      (socket.ev as any).on("messages.update", (updates: any[]) => {
        void this.handleMessageUpdates(account, updates || []).catch((error) => {
          this.logger.error({ account_id: accountId, error }, "whatsapp_messages_update_failed");
        });
      });
      (socket.ev as any).on("message-receipt.update", (updates: any[]) => {
        void this.handleReceiptUpdates(account, updates || []).catch((error) => {
          this.logger.error({ account_id: accountId, error }, "whatsapp_message_receipt_update_failed");
        });
      });
      if (socket.ws && typeof (socket.ws as any).on === "function") {
        (socket.ws as any).on("error", (error: unknown) => {
          this.logger.warn({ account_id: accountId, error }, "whatsapp_websocket_error");
        });
        (socket.ws as any).on("message", () => {
          account.status.last_transport_activity_at = new Date().toISOString();
        });
      }
      return account.status;
    } catch (error) {
      account.status = {
        ...account.status,
        status: "error",
        authentication_state: this.sessions.credentialsPersisted(accountId) ? "linked" : "error",
        listener_state: "error",
        last_error_code: errorCode(error, "whatsapp_start_failed"),
        last_error_message: error instanceof Error ? error.message : String(error)
      };
      await this.emitStatus(account);
      await this.scheduleReconnect(account);
      return account.status;
    }
  }

  async stop(accountId: string): Promise<AccountSnapshot> {
    const account = this.account(accountId);
    account.stopped = true;
    this.clearQrTimer(account);
    this.clearReconnectTimer(account);
    this.closeSocket(account, true);
    account.owner?.release();
    account.owner = undefined;
    account.status = {
      ...baseSnapshot(
        accountId,
        account.generation,
        this.sessions.credentialsPersisted(accountId)
      ),
      last_disconnected_at: new Date().toISOString(),
      reconnect_count: account.status.reconnect_count
    };
    await this.emitStatus(account);
    return account.status;
  }

  async logout(accountId: string): Promise<AccountSnapshot> {
    const account = this.account(accountId);
    account.stopped = true;
    this.clearQrTimer(account);
    this.clearReconnectTimer(account);
    const socket = account.socket;
    if (socket) {
      account.suppressReconnectFor = socket;
      try {
        await socket.logout();
      } catch (error) {
        this.logger.warn({ account_id: accountId, error }, "whatsapp_logout_socket_failed");
      }
    }
    this.closeSocket(account, true);
    this.sessions.clearCredentials(accountId);
    account.owner?.release();
    account.owner = undefined;
    account.pairingUntilMs = undefined;
    account.status = {
      ...baseSnapshot(accountId, account.generation, false),
      status: "disconnected",
      authentication_state: "revoked",
      last_disconnected_at: new Date().toISOString()
    };
    await this.emitStatus(account);
    return account.status;
  }

  async restart(accountId: string, generation?: number): Promise<AccountSnapshot> {
    const account = this.account(accountId);
    if (generation !== undefined) account.generation = Math.max(0, generation);
    await this.stop(accountId);
    return await this.start(accountId, account.generation);
  }

  async status(accountId: string): Promise<AccountSnapshot> {
    const account = this.accounts.get(accountId);
    if (account) {
      if (
        account.status.qr_status === "pending" &&
        account.status.qr_expires_at &&
        Date.parse(account.status.qr_expires_at) <= Date.now()
      ) {
        this.expireQr(account);
      }
      return account.status;
    }
    return baseSnapshot(accountId, 0, this.sessions.credentialsPersisted(accountId));
  }

  async requestPairingCode(
    accountId: string,
    request: PairingCodeRequest
  ): Promise<PairingCodeResult> {
    const digits = request.phone_number.replace(/\D/g, "");
    if (!/^\d{8,16}$/.test(digits)) {
      return {
        ok: false,
        account_id: accountId,
        error_code: "invalid_phone_number",
        retryable: false
      };
    }
    const account = this.account(accountId);
    if (account.status.status === "connected") {
      return {
        ok: false,
        account_id: accountId,
        error_code: "already_connected",
        retryable: false
      };
    }

    let lastErrorCode = "whatsapp_socket_not_ready";
    let retryable = true;
    for (let attempt = 1; attempt <= PAIRING_CODE_ATTEMPTS; attempt += 1) {
      await this.stop(accountId);
      this.sessions.resetAccount(accountId);
      await this.start(accountId, account.generation);
      if (account.socket) {
        const socket = account.socket;
        await sleep(PAIRING_CODE_READY_DELAY_MS);
        if (account.socket !== socket) continue;
        try {
          const code = await socket.requestPairingCode(digits);
          account.pairingUntilMs = Date.now() + PAIRING_CODE_WINDOW_MS;
          account.status = {
            ...account.status,
            status: "auth_persisting",
            authentication_state: "pending",
            listener_state: "starting"
          };
          await this.emitStatus(account);
          return {
            ok: true,
            account_id: accountId,
            pairing_code: code,
            phone_number_suffix: digits.slice(-4),
            expires_at: new Date(account.pairingUntilMs).toISOString()
          };
        } catch (error) {
          lastErrorCode = errorCode(error, "pairing_code_request_failed");
          retryable = isRetryablePairingError(error);
          this.logger.warn(
            {
              account_id: accountId,
              attempt,
              attempts: PAIRING_CODE_ATTEMPTS,
              error_code: lastErrorCode,
              phone_number_suffix: digits.slice(-4),
              retryable,
              status_code: errorStatusCode(error)
            },
            "pairing_code_request_failed"
          );
          this.closeSocket(account, true);
          if (!retryable) break;
        }
      }
      if (attempt < PAIRING_CODE_ATTEMPTS && retryable) {
        await sleep(PAIRING_CODE_RETRY_DELAY_MS);
      }
    }
    account.pairingUntilMs = undefined;
    await this.stop(accountId);
    this.sessions.resetAccount(accountId);
    return {
      ok: false,
      account_id: accountId,
      error_code: lastErrorCode,
      error_message: lastErrorCode,
      retryable
    };
  }

  async send(accountId: string, request: SendRequest): Promise<SendResult> {
    const cached = this.sessions.readSendResult(
      accountId,
      request.idempotency_key,
      this.config.idempotencyTtlMs
    );
    if (cached) return cached;
    const account = this.account(accountId);
    let result: SendResult;
    if (account.status.status !== "connected" || !account.socket) {
      result = notConnectedResult();
    } else {
      const jid = targetToWhatsAppJid(request.chat_jid) || targetToWhatsAppJid(request.target);
      if (!jid) {
        result = missingTargetResult();
      } else {
        try {
          const sent = await account.socket.sendMessage(jid, { text: request.body });
          result = sendResult(sent?.key?.id || null);
          if (result.ok && result.sent_at) {
            account.status.last_outbound_at = result.sent_at;
            account.status.last_transport_activity_at = result.sent_at;
          }
        } catch (error) {
          result = sendFailure(error);
        }
      }
    }
    this.sessions.writeSendResult(accountId, request.idempotency_key, result);
    return result;
  }

  async sendMedia(
    accountId: string,
    request: SendMediaRequest,
    content: Buffer
  ): Promise<SendResult> {
    const cached = this.sessions.readSendResult(
      accountId,
      request.idempotency_key,
      this.config.idempotencyTtlMs
    );
    if (cached) return cached;
    const account = this.account(accountId);
    let result: SendResult;
    if (account.status.status !== "connected" || !account.socket) {
      result = notConnectedResult();
    } else if (!content.byteLength || content.byteLength > MEDIA_LIMITS[request.media_kind]) {
      result = {
        ok: false,
        status: "failed",
        error_code: "whatsapp_media_size_invalid",
        retryable: false
      };
    } else {
      const jid = targetToWhatsAppJid(request.chat_jid) || targetToWhatsAppJid(request.target);
      if (!jid) {
        result = missingTargetResult();
      } else {
        try {
          const message = mediaMessageContent(request, content);
          const sent = await account.socket.sendMessage(jid, message as any);
          result = sendResult(sent?.key?.id || null);
          if (result.ok && result.sent_at) {
            account.status.last_outbound_at = result.sent_at;
            account.status.last_transport_activity_at = result.sent_at;
          }
        } catch (error) {
          result = sendFailure(error);
        }
      }
    }
    this.sessions.writeSendResult(accountId, request.idempotency_key, result);
    return result;
  }

  pendingMediaDownloads(): number {
    return this.mediaDownloads.count();
  }

  private account(accountId: string): RuntimeAccount {
    let account = this.accounts.get(accountId);
    if (!account) {
      account = {
        accountId,
        generation: 0,
        status: baseSnapshot(accountId, 0, this.sessions.credentialsPersisted(accountId)),
        stopped: true,
        reconnectAttempts: 0
      };
      this.accounts.set(accountId, account);
    }
    return account;
  }

  private async handleConnectionUpdate(
    account: RuntimeAccount,
    socket: WASocket,
    update: any
  ): Promise<void> {
    account.status.last_transport_activity_at = new Date().toISOString();
    if (update.qr) {
      const generatedAt = new Date();
      account.status = {
        ...account.status,
        status: "qr_pending",
        authentication_state: "pending",
        listener_state: "starting",
        qr_status: "pending",
        qr: update.qr,
        qr_data_url: await qrDataUrl(update.qr),
        last_qr_generated_at: generatedAt.toISOString(),
        qr_expires_at: new Date(generatedAt.getTime() + this.config.qrTtlMs).toISOString()
      };
      this.clearQrTimer(account);
      account.qrTimer = setTimeout(() => this.expireQr(account), this.config.qrTtlMs);
      account.qrTimer.unref?.();
      await this.emitStatus(account);
    }
    if (update.connection === "open") {
      this.clearQrTimer(account);
      account.status = {
        ...account.status,
        status: "auth_persisting",
        authentication_state: "pending",
        listener_state: "starting",
        qr_status: "consumed",
        qr: null,
        qr_data_url: null,
        qr_expires_at: null
      };
      await this.emitStatus(account);
      const persisted = await this.sessions.waitForCredentials(
        account.accountId,
        this.config.credentialPersistenceTimeoutMs
      );
      if (!persisted) {
        this.abortForCredentialFailure(
          account,
          socket,
          new Error("whatsapp_credentials_not_persisted")
        );
        return;
      }
      const jid = socket.user?.id || null;
      account.reconnectAttempts = 0;
      account.status = {
        ...account.status,
        status: "connected",
        authentication_state: "linked",
        listener_state: "active",
        qr_status: "consumed",
        jid,
        phone_number: jid ? `+${jid.split("@")[0].split(":")[0].replace(/\D/g, "")}` : null,
        last_connected_at: new Date().toISOString(),
        last_error_code: null,
        last_error_message: null
      };
      await this.emitStatus(account);
      void this.drainMediaDownloads(account);
    }
    if (update.connection === "close") {
      const suppressed = account.suppressReconnectFor === socket;
      if (suppressed) account.suppressReconnectFor = undefined;
      if (account.socket !== socket) return;
      account.socket = undefined;
      const statusCode = (update.lastDisconnect?.error as Boom | undefined)?.output?.statusCode;
      const loggedOut = statusCode === DisconnectReason.loggedOut;
      account.status = {
        ...account.status,
        status: loggedOut ? "disconnected" : "reconnecting",
        authentication_state: loggedOut ? "revoked" : account.status.authentication_state,
        listener_state: loggedOut ? "stopped" : "reconnecting",
        last_disconnected_at: new Date().toISOString(),
        last_error_code: statusCode ? String(statusCode) : "socket_closed",
        last_error_message: update.lastDisconnect?.error?.message || "socket closed",
        reconnect_count: account.status.reconnect_count + (loggedOut ? 0 : 1)
      };
      await this.emitStatus(account);
      if (loggedOut || suppressed || account.stopped) {
        account.owner?.release();
        account.owner = undefined;
        return;
      }
      await this.scheduleReconnect(account);
    }
  }

  private async handleMessages(account: RuntimeAccount, messages: any[]): Promise<void> {
    for (const raw of messages) {
      const normalized = normalizeBaileysInbound(account.accountId, raw, {
        allowFromMeInbound: this.config.allowFromMeInbound,
        fromMeMode: this.config.fromMeMode,
        fromMeTestPrefix: this.config.fromMeTestPrefix
      });
      if (!normalized) continue;
      const projected = projectSelfTestInboundToPhoneJid(normalized, account.status);
      const hasMedia = Boolean(
        projected.media_kind &&
        projected.media_mime_type &&
        projected.external_message_id
      );
      if (hasMedia) {
        this.mediaDownloads.enqueue({
          accountId: account.accountId,
          externalMessageId: projected.external_message_id,
          mediaKind: projected.media_kind as WhatsAppMediaKind,
          mediaType: projected.media_mime_type as string,
          fileName: projected.media_filename,
          rawMessage: raw
        });
      }
      await this.onInbound(projected);
      if (hasMedia) await this.drainMediaDownloads(account);
      const occurredAt = new Date().toISOString();
      account.status.last_inbound_at = occurredAt;
      account.status.last_transport_activity_at = occurredAt;
      await this.emitStatus(account);
    }
  }

  private async handleMessageUpdates(account: RuntimeAccount, updates: any[]): Promise<void> {
    for (const item of updates) {
      const status = deliveryStatusFromMessageUpdate(item?.update);
      if (!status) continue;
      await this.publishDeliveryReceipt(
        account,
        item?.key,
        status,
        providerTimestampIso(item?.update?.messageTimestamp || item?.update?.timestamp)
      );
    }
  }

  private async handleReceiptUpdates(account: RuntimeAccount, updates: any[]): Promise<void> {
    for (const item of updates) {
      const status = deliveryStatusFromReceiptUpdate(item?.receipt);
      if (!status) continue;
      const timestamp =
        item?.receipt?.playedTimestamp ||
        item?.receipt?.readTimestamp ||
        item?.receipt?.receiptTimestamp ||
        item?.receipt?.deliveredTimestamp;
      await this.publishDeliveryReceipt(account, item?.key, status, providerTimestampIso(timestamp));
    }
  }

  private async publishDeliveryReceipt(
    account: RuntimeAccount,
    key: any,
    status: RecipientDeliveryStatus,
    occurredAt: string
  ): Promise<void> {
    const providerMessageId = String(key?.id || "").trim();
    if (!providerMessageId || key?.fromMe === false) return;
    account.status.last_transport_activity_at = occurredAt;
    await this.onDelivery(account.accountId, {
      account_id: account.accountId,
      provider_message_id: providerMessageId,
      status,
      occurred_at: occurredAt,
      idempotency_key: `baileys-receipt:${providerMessageId}:${status}`,
      metadata: {}
    });
  }

  private async drainAllMediaDownloads(): Promise<void> {
    for (const account of this.accounts.values()) {
      if (account.socket && account.status.status === "connected") {
        await this.drainMediaDownloads(account);
      }
    }
  }

  private async drainMediaDownloads(account: RuntimeAccount): Promise<void> {
    if (this.mediaDrainAccounts.has(account.accountId)) return;
    this.mediaDrainAccounts.add(account.accountId);
    try {
      const result = await this.mediaDownloads.drainAccount(
        account.accountId,
        async (envelope) => this.downloadAndUploadMedia(account, envelope)
      );
      if (result.delivered || result.pending || result.dead) {
        this.logger.info(
          {
            account_id: account.accountId,
            delivered: result.delivered,
            pending: result.pending,
            dead: result.dead
          },
          "baileys_media_download_outbox_drained"
        );
      }
    } finally {
      this.mediaDrainAccounts.delete(account.accountId);
    }
  }

  private async downloadAndUploadMedia(
    account: RuntimeAccount,
    envelope: MediaDownloadEnvelope
  ): Promise<void> {
    if (!account.socket || account.status.status !== "connected") {
      throw mediaDownloadError("baileys_media_socket_not_ready", true);
    }
    const downloaded = await downloadMediaMessage(
      envelope.raw_message as any,
      "buffer",
      {},
      {
        logger: this.logger.child({ account_id: account.accountId }) as any,
        reuploadRequest: account.socket.updateMediaMessage
      }
    );
    const content = Buffer.isBuffer(downloaded) ? downloaded : Buffer.from(downloaded as any);
    if (!content.byteLength || content.byteLength > MEDIA_LIMITS[envelope.media_kind]) {
      throw mediaDownloadError("baileys_media_size_rejected", false);
    }
    await this.onMedia({
      accountId: account.accountId,
      messageId: envelope.external_message_id,
      mediaKind: envelope.media_kind,
      mediaType: envelope.media_type,
      filename: envelope.file_name,
      content
    });
  }

  private async scheduleReconnect(account: RuntimeAccount): Promise<void> {
    if (account.stopped || account.reconnectTimer) return;
    if (account.reconnectAttempts >= this.config.reconnectMaxAttempts) {
      account.status = {
        ...account.status,
        status: "error",
        authentication_state: this.sessions.credentialsPersisted(account.accountId) ? "linked" : "error",
        listener_state: "error",
        last_error_code: "reconnect_attempts_exhausted",
        last_error_message: "Reconnect attempts exhausted"
      };
      account.owner?.release();
      account.owner = undefined;
      await this.emitStatus(account);
      return;
    }
    const base = Math.min(
      this.config.reconnectInitialMs * 1.8 ** account.reconnectAttempts,
      this.config.reconnectMaxMs
    );
    const jitter = base * this.config.reconnectJitter * (Math.random() * 2 - 1);
    const delayMs = Math.max(250, Math.round(base + jitter));
    account.reconnectAttempts += 1;
    account.reconnectTimer = setTimeout(() => {
      account.reconnectTimer = undefined;
      void this.start(account.accountId, account.generation).catch((error) => {
        this.logger.error({ account_id: account.accountId, error }, "whatsapp_reconnect_failed");
      });
    }, delayMs);
    account.reconnectTimer.unref?.();
  }

  private abortForCredentialFailure(
    account: RuntimeAccount,
    socket: WASocket,
    error: unknown
  ): void {
    if (account.socket === socket) account.socket = undefined;
    account.suppressReconnectFor = socket;
    try {
      socket.end(error instanceof Error ? error : new Error(String(error)));
    } catch {
      // best effort
    }
    account.status = {
      ...account.status,
      status: "error",
      authentication_state: "unstable",
      listener_state: "error",
      last_error_code: "credential_persistence_failed",
      last_error_message: error instanceof Error ? error.message : String(error)
    };
    void this.emitStatus(account);
  }

  private closeSocket(account: RuntimeAccount, suppressReconnect: boolean): void {
    const socket = account.socket;
    if (!socket) return;
    account.socket = undefined;
    if (suppressReconnect) account.suppressReconnectFor = socket;
    try {
      socket.end(undefined);
    } catch {
      // Socket may already be closed.
    }
  }

  private expireQr(account: RuntimeAccount): void {
    if (account.status.qr_status !== "pending") return;
    account.status = {
      ...account.status,
      status: "connecting",
      qr_status: "expired",
      qr: null,
      qr_data_url: null,
      qr_expires_at: null
    };
    void this.emitStatus(account);
  }

  private clearQrTimer(account: RuntimeAccount): void {
    if (account.qrTimer) clearTimeout(account.qrTimer);
    account.qrTimer = undefined;
  }

  private clearReconnectTimer(account: RuntimeAccount): void {
    if (account.reconnectTimer) clearTimeout(account.reconnectTimer);
    account.reconnectTimer = undefined;
  }

  private async emitStatus(account: RuntimeAccount): Promise<void> {
    account.status.generation = account.generation;
    await this.onStatus(account.accountId, account.status);
  }
}

function notConnectedResult(): SendResult {
  return {
    ok: false,
    status: "failed",
    error_code: "whatsapp_not_connected",
    retryable: true
  };
}

function missingTargetResult(): SendResult {
  return {
    ok: false,
    status: "failed",
    error_code: "missing_target",
    retryable: false
  };
}

function sendResult(providerMessageId: string | null): SendResult {
  if (!providerMessageId) {
    return {
      ok: false,
      status: "failed",
      error_code: "provider_message_id_missing",
      retryable: true
    };
  }
  return {
    ok: true,
    status: "sent",
    provider_message_id: providerMessageId,
    sent_at: new Date().toISOString()
  };
}

function sendFailure(error: unknown): SendResult {
  return {
    ok: false,
    status: "failed",
    error_code: errorCode(error, "whatsapp_send_failed"),
    retryable: true
  };
}

function mediaMessageContent(request: SendMediaRequest, content: Buffer): Record<string, unknown> {
  const caption = String(request.caption || "").trim() || undefined;
  const mimetype = String(request.media_type || "application/octet-stream").split(";", 1)[0].trim().toLowerCase();
  if (request.media_kind === "image") return { image: content, mimetype, caption };
  if (request.media_kind === "video") return { video: content, mimetype, caption };
  if (request.media_kind === "audio") return { audio: content, mimetype, ptt: false };
  if (request.media_kind === "sticker") return { sticker: content };
  return {
    document: content,
    mimetype,
    fileName: String(request.filename || "document").slice(0, 255),
    caption
  };
}

export function targetToWhatsAppJid(target: string | null | undefined): string | null {
  const trimmed = (target || "").trim();
  if (!trimmed || trimmed === "status@broadcast") return null;
  if (trimmed.endsWith("@broadcast") || trimmed.endsWith("@g.us") || trimmed.endsWith("@newsletter")) return null;
  if (trimmed.endsWith("@s.whatsapp.net") || trimmed.endsWith("@lid")) return trimmed;
  if (trimmed.includes("@")) return null;
  const digits = trimmed.replace(/\D/g, "");
  return digits ? `${digits}@s.whatsapp.net` : null;
}

export function phoneJidFromAccountSnapshot(
  status: Pick<AccountSnapshot, "phone_number" | "jid">
): string | null {
  const phoneDigits = (status.phone_number || "").replace(/\D/g, "");
  if (phoneDigits) return `${phoneDigits}@s.whatsapp.net`;
  const jidDigits = (status.jid || "").split("@")[0]?.split(":")[0]?.replace(/\D/g, "") || "";
  return jidDigits ? `${jidDigits}@s.whatsapp.net` : null;
}

export function projectSelfTestInboundToPhoneJid(
  message: NormalizedInboundMessage,
  status: Pick<AccountSnapshot, "phone_number" | "jid">
): NormalizedInboundMessage {
  if (message.from_me !== true || message.projection_mode !== "test_visitor") return message;
  const selfPhoneJid = phoneJidFromAccountSnapshot(status);
  if (!selfPhoneJid) return message;
  return {
    ...message,
    chat_jid: selfPhoneJid,
    sender_jid: selfPhoneJid,
    sender_phone: `+${selfPhoneJid.split("@")[0]}`,
    raw_message:
      message.raw_message && typeof message.raw_message === "object" && !Array.isArray(message.raw_message)
        ? {
            ...(message.raw_message as Record<string, unknown>),
            nexus_self_test_original_chat_jid: message.chat_jid,
            nexus_self_test_projected_chat_jid: selfPhoneJid
          }
        : message.raw_message
  };
}
