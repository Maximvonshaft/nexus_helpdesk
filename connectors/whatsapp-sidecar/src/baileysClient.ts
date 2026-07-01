import type { Logger } from "pino";
import { Boom } from "@hapi/boom";
import makeWASocket, {
  DisconnectReason,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
  useMultiFileAuthState,
  type WASocket
} from "@whiskeysockets/baileys";
import { normalizeBaileysInbound } from "./inboundMapper.js";
import { qrDataUrl } from "./qrManager.js";
import type { AccountSnapshot, NormalizedInboundMessage, PairingCodeRequest, PairingCodeResult, SendRequest, SendResult, SidecarConfig, WhatsAppConnector } from "./types.js";
import { SessionStore } from "./sessionStore.js";

type InboundHandler = (message: NormalizedInboundMessage) => Promise<void>;
type StatusHandler = (accountId: string, snapshot: AccountSnapshot) => Promise<void>;

const PAIRING_CODE_ATTEMPTS = 5;
const PAIRING_CODE_READY_DELAY_MS = 1500;
const PAIRING_CODE_RETRY_DELAY_MS = 2000;
const PAIRING_CODE_WINDOW_MS = 180_000;
const PAIRING_CODE_RECONNECT_DELAY_MS = 2000;

interface RuntimeAccount {
  accountId: string;
  socket?: WASocket;
  suppressReconnectFor?: WASocket;
  pairingUntilMs?: number;
  qrExpireTimer?: NodeJS.Timeout;
  reconnectTimer?: NodeJS.Timeout;
  status: AccountSnapshot;
  idempotency: Map<string, SendResult>;
}

function baseSnapshot(accountId: string): AccountSnapshot {
  return {
    account_id: accountId,
    status: "idle",
    qr_status: "none",
    session_state: "empty",
    reconnect_count: 0
  };
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function withTimeout<T>(operation: string, timeoutMs: number, promise: Promise<T>): Promise<T> {
  let timeout: NodeJS.Timeout | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<T>((_, reject) => {
        timeout = setTimeout(() => {
          reject(new Error(`${operation}_timeout`));
        }, timeoutMs);
        timeout.unref?.();
      })
    ]);
  } finally {
    if (timeout) clearTimeout(timeout);
  }
}

function errorStatusCode(error: unknown): number | undefined {
  const outputStatus = (error as { output?: { statusCode?: unknown } })?.output?.statusCode;
  if (typeof outputStatus === "number") return outputStatus;
  const statusCode = (error as { statusCode?: unknown })?.statusCode;
  if (typeof statusCode === "number") return statusCode;
  const status = (error as { status?: unknown })?.status;
  return typeof status === "number" ? status : undefined;
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

export class BaileysConnector implements WhatsAppConnector {
  private readonly accounts = new Map<string, RuntimeAccount>();

  constructor(
    private readonly sessions: SessionStore,
    private readonly logger: Logger,
    private readonly onInbound: InboundHandler,
    private readonly onStatus: StatusHandler,
    private readonly config: Pick<
      SidecarConfig,
      | "browserPlatform"
      | "browserName"
      | "browserVersion"
      | "keepAliveIntervalMs"
      | "connectTimeoutMs"
      | "defaultQueryTimeoutMs"
      | "operationTimeoutMs"
      | "qrTtlMs"
      | "reconnectBaseDelayMs"
      | "reconnectMaxDelayMs"
      | "reconnectMaxAttempts"
      | "allowFromMeInbound"
      | "fromMeMode"
      | "fromMeTestPrefix"
      | "baileysLogLevel"
    >
  ) {}

  async start(accountId: string): Promise<AccountSnapshot> {
    const account = this.account(accountId);
    const canReuseSocket =
      account.status.status === "connected" ||
      account.status.status === "connecting" ||
      (account.status.status === "qr_pending" && account.status.qr_status === "pending");
    if (account.socket && canReuseSocket) {
      return account.status;
    }
    if (account.socket) {
      this.closeSocket(account, true);
    }
    this.clearReconnectTimer(account);
    const restoredBackup = this.sessions.restoreCredsBackupIfNeeded(accountId);
    const session = this.sessions.inspectAccount(accountId);
    account.status = {
      ...account.status,
      status: "connecting",
      qr_status: "none",
      qr: null,
      qr_data_url: null,
      last_qr_expires_at: null,
      last_error_code: restoredBackup ? "restored_creds_backup" : null,
      last_error_message: restoredBackup ? "Restored WhatsApp credentials from backup before connecting" : null,
      session_state: session.state,
      jid: session.jid,
      phone_number: session.phoneNumber,
      browser: this.browserTuple()
    };
    await this.emitStatus(account);

    const { state, saveCreds } = await useMultiFileAuthState(this.sessions.accountPath(accountId));
    const { version } = await fetchLatestBaileysVersion();
    const socketLogger = this.logger.child({ account_id: accountId, subsystem: "baileys" }) as any;
    socketLogger.level = this.config.baileysLogLevel;
    const socket = makeWASocket({
      version,
      auth: {
        creds: state.creds,
        keys: makeCacheableSignalKeyStore(state.keys, socketLogger)
      },
      browser: this.browserTuple(),
      syncFullHistory: false,
      markOnlineOnConnect: false,
      keepAliveIntervalMs: this.config.keepAliveIntervalMs,
      connectTimeoutMs: this.config.connectTimeoutMs,
      defaultQueryTimeoutMs: this.config.defaultQueryTimeoutMs,
      printQRInTerminal: false,
      logger: socketLogger
    });
    account.socket = socket;
    socket.ev.on("creds.update", () => {
      void (async () => {
        try {
          this.sessions.backupCreds(accountId);
          await saveCreds();
          const savedSession = this.sessions.inspectAccount(accountId);
          account.status = {
            ...account.status,
            session_state: savedSession.state,
            jid: savedSession.jid || account.status.jid || null,
            phone_number: savedSession.phoneNumber || account.status.phone_number || null
          };
        } catch (error) {
          this.logger.warn({ account_id: accountId, error }, "whatsapp_creds_save_failed");
        }
      })();
    });
    socket.ev.on("connection.update", async (update) => {
      account.status = { ...account.status, last_transport_at: new Date().toISOString() };
      if (update.qr) {
        const expiresAt = new Date(Date.now() + this.config.qrTtlMs).toISOString();
        account.status = {
          ...account.status,
          status: "qr_pending",
          qr_status: "pending",
          qr: update.qr,
          qr_data_url: await qrDataUrl(update.qr),
          last_qr_generated_at: new Date().toISOString(),
          last_qr_expires_at: expiresAt,
          session_state: this.sessions.inspectAccount(accountId).state
        };
        this.scheduleQrExpiry(account, update.qr);
        await this.emitStatus(account);
      }
      if (update.connection === "open") {
        this.clearQrTimer(account);
        account.pairingUntilMs = undefined;
        const session = this.sessions.inspectAccount(accountId);
        const jid = socket.user?.id || session.jid || null;
        account.status = {
          ...account.status,
          status: "connected",
          qr_status: "consumed",
          qr: null,
          qr_data_url: null,
          last_qr_expires_at: null,
          jid,
          phone_number: jid ? `+${jid.split("@")[0].split(":")[0].replace(/\D/g, "")}` : null,
          last_connected_at: new Date().toISOString(),
          last_error_code: null,
          last_error_message: null,
          session_state: session.state === "empty" && jid ? "partial" : session.state
        };
        await this.emitStatus(account);
      }
      if (update.connection === "close") {
        this.clearQrTimer(account);
        const suppressReconnect = account.suppressReconnectFor === socket;
        if (suppressReconnect) {
          account.suppressReconnectFor = undefined;
        }
        if (account.socket !== socket) {
          return;
        }
        const statusCode = (update.lastDisconnect?.error as Boom | undefined)?.output?.statusCode;
        const pairingInProgress = this.isPairingInProgress(account);
        const terminal = this.isTerminalDisconnect(statusCode, pairingInProgress);
        const reconnectCount = account.status.reconnect_count + (terminal ? 0 : 1);
        const reconnectExhausted = !terminal && reconnectCount > this.config.reconnectMaxAttempts;
        const nextStatus = terminal ? "disconnected" : reconnectExhausted ? "error" : "reconnecting";
        account.status = {
          ...account.status,
          status: nextStatus,
          qr_status: account.status.qr_status === "pending" ? "expired" : account.status.qr_status,
          qr: null,
          qr_data_url: null,
          last_qr_expires_at: null,
          last_disconnected_at: new Date().toISOString(),
          last_error_code: this.disconnectCode(statusCode),
          last_error_message: update.lastDisconnect?.error?.message || "socket closed",
          reconnect_count: reconnectCount,
          session_state: this.sessions.inspectAccount(accountId).state
        };
        await this.emitStatus(account);
        if (!terminal && !reconnectExhausted && !suppressReconnect) {
          account.socket = undefined;
          this.scheduleReconnect(account, accountId, pairingInProgress ? PAIRING_CODE_RECONNECT_DELAY_MS : undefined);
        }
      }
    });
    socket.ev.on("messages.upsert", async ({ messages }) => {
      for (const raw of messages || []) {
        const normalized = normalizeBaileysInbound(accountId, raw, {
          allowFromMeInbound: this.config.allowFromMeInbound,
          fromMeMode: this.config.fromMeMode,
          fromMeTestPrefix: this.config.fromMeTestPrefix
        });
        if (normalized) {
          await this.onInbound(normalized);
        }
      }
    });
    return account.status;
  }

  async logout(accountId: string): Promise<AccountSnapshot> {
    const account = this.account(accountId);
    this.clearReconnectTimer(account);
    this.clearQrTimer(account);
    if (account.socket) {
      await account.socket.logout().catch((error) => {
        this.logger.warn({ account_id: accountId, error }, "whatsapp_logout_failed");
      });
      account.socket = undefined;
    }
    account.pairingUntilMs = undefined;
    this.sessions.resetAccount(accountId);
    account.status = { ...baseSnapshot(accountId), status: "disconnected" };
    await this.emitStatus(account);
    return account.status;
  }

  async restart(accountId: string): Promise<AccountSnapshot> {
    const account = this.account(accountId);
    this.closeSocket(account, true);
    this.clearReconnectTimer(account);
    return this.start(accountId);
  }

  async status(accountId: string): Promise<AccountSnapshot> {
    return this.account(accountId).status;
  }

  async requestPairingCode(accountId: string, request: PairingCodeRequest): Promise<PairingCodeResult> {
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
      this.closeSocket(account, true);
      this.sessions.resetAccount(accountId);
      await this.start(accountId);

      if (account.socket) {
        const socket = account.socket;
        await sleep(PAIRING_CODE_READY_DELAY_MS);
        if (account.socket !== socket) {
          continue;
        }
        try {
          const code = await socket.requestPairingCode(digits);
          account.pairingUntilMs = Date.now() + PAIRING_CODE_WINDOW_MS;
          return {
            ok: true,
            account_id: accountId,
            pairing_code: code,
            phone_number_suffix: digits.slice(-4)
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
    this.closeSocket(account, true);
    this.sessions.resetAccount(accountId);
    return {
      ok: false,
      account_id: accountId,
      error_code: lastErrorCode,
      retryable
    };
  }

  async send(accountId: string, request: SendRequest): Promise<SendResult> {
    const account = this.account(accountId);
    const existing = account.idempotency.get(request.idempotency_key);
    if (existing) return existing;
    if (account.status.status !== "connected" || !account.socket) {
      return this.cacheSend(account, request.idempotency_key, {
        ok: false,
        status: "failed",
        error_code: "whatsapp_not_connected",
        retryable: true
      });
    }
    const jid = request.chat_jid || this.targetToJid(request.target);
    if (!jid) {
      return this.cacheSend(account, request.idempotency_key, {
        ok: false,
        status: "failed",
        error_code: "missing_target",
        retryable: false
      });
    }
    try {
      const result = await withTimeout(
        "sendMessage",
        this.config.operationTimeoutMs,
        account.socket.sendMessage(jid, { text: request.body })
      );
      return this.cacheSend(account, request.idempotency_key, {
        ok: true,
        status: "sent",
        provider_message_id: result?.key?.id || null,
        sent_at: new Date().toISOString()
      });
    } catch (error) {
      return this.cacheSend(account, request.idempotency_key, {
        ok: false,
        status: "failed",
        error_code: errorCode(error, "whatsapp_send_failed"),
        error_message: error instanceof Error ? error.message : String(error),
        retryable: true
      });
    }
  }

  private account(accountId: string): RuntimeAccount {
    let account = this.accounts.get(accountId);
    if (!account) {
      account = { accountId, status: baseSnapshot(accountId), idempotency: new Map() };
      this.accounts.set(accountId, account);
    }
    return account;
  }

  private closeSocket(account: RuntimeAccount, suppressReconnect: boolean): void {
    const socket = account.socket;
    if (!socket) return;
    account.socket = undefined;
    this.clearQrTimer(account);
    this.clearReconnectTimer(account);
    if (suppressReconnect) {
      account.suppressReconnectFor = socket;
    }
    try {
      socket.end(undefined);
    } catch {
      // Socket may already be closed by Baileys.
    }
  }

  private targetToJid(target: string | null | undefined): string | null {
    const digits = (target || "").replace(/\D/g, "");
    return digits ? `${digits}@s.whatsapp.net` : null;
  }

  private isPairingInProgress(account: RuntimeAccount): boolean {
    return typeof account.pairingUntilMs === "number" && account.pairingUntilMs > Date.now();
  }

  private browserTuple(): [string, string, string] {
    return [this.config.browserPlatform, this.config.browserName, this.config.browserVersion];
  }

  private clearQrTimer(account: RuntimeAccount): void {
    if (account.qrExpireTimer) {
      clearTimeout(account.qrExpireTimer);
      account.qrExpireTimer = undefined;
    }
  }

  private clearReconnectTimer(account: RuntimeAccount): void {
    if (account.reconnectTimer) {
      clearTimeout(account.reconnectTimer);
      account.reconnectTimer = undefined;
    }
  }

  private scheduleQrExpiry(account: RuntimeAccount, qr: string): void {
    this.clearQrTimer(account);
    account.qrExpireTimer = setTimeout(() => {
      if (account.status.qr !== qr || account.status.qr_status !== "pending") return;
      account.status = {
        ...account.status,
        qr_status: "expired",
        qr: null,
        qr_data_url: null,
        last_qr_expires_at: null,
        last_error_code: "qr_expired",
        last_error_message: "WhatsApp QR expired before it was linked"
      };
      void this.emitStatus(account).catch((error) => {
        this.logger.warn({ account_id: account.accountId, error }, "whatsapp_qr_expiry_status_failed");
      });
    }, this.config.qrTtlMs);
    account.qrExpireTimer.unref?.();
  }

  private scheduleReconnect(account: RuntimeAccount, accountId: string, minimumDelayMs?: number): void {
    this.clearReconnectTimer(account);
    const attempt = Math.max(1, account.status.reconnect_count);
    const exponentialDelay = this.config.reconnectBaseDelayMs * (2 ** Math.min(attempt - 1, 6));
    const delayMs = Math.max(minimumDelayMs || 0, Math.min(this.config.reconnectMaxDelayMs, exponentialDelay));
    account.reconnectTimer = setTimeout(() => {
      account.reconnectTimer = undefined;
      void this.start(accountId).catch((error) => {
        this.logger.error({ account_id: accountId, error }, "whatsapp_reconnect_failed");
      });
    }, delayMs);
    account.reconnectTimer.unref?.();
  }

  private isTerminalDisconnect(statusCode: number | undefined, pairingInProgress: boolean): boolean {
    if (pairingInProgress && statusCode === DisconnectReason.loggedOut) return false;
    return [
      DisconnectReason.loggedOut,
      DisconnectReason.forbidden,
      DisconnectReason.multideviceMismatch,
      DisconnectReason.connectionReplaced,
      DisconnectReason.badSession
    ].includes(statusCode as DisconnectReason);
  }

  private disconnectCode(statusCode: number | undefined): string {
    if (!statusCode) return "socket_closed";
    const label = DisconnectReason[statusCode as DisconnectReason];
    return label ? `disconnect_${label}` : `disconnect_${statusCode}`;
  }

  private cacheSend(account: RuntimeAccount, key: string, result: SendResult): SendResult {
    account.idempotency.set(key, result);
    return result;
  }

  private async emitStatus(account: RuntimeAccount): Promise<void> {
    try {
      await this.onStatus(account.accountId, account.status);
    } catch (error) {
      this.logger.warn(
        {
          account_id: account.accountId,
          error_code: errorCode(error, "backend_status_callback_failed"),
          status_code: errorStatusCode(error)
        },
        "whatsapp_status_callback_failed"
      );
    }
  }
}
