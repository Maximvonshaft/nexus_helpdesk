import type { Logger } from "pino";
import { Boom } from "@hapi/boom";
import makeWASocket, {
  Browsers,
  DisconnectReason,
  fetchLatestBaileysVersion,
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
  status: AccountSnapshot;
  idempotency: Map<string, SendResult>;
}

function baseSnapshot(accountId: string): AccountSnapshot {
  return {
    account_id: accountId,
    status: "idle",
    qr_status: "none",
    reconnect_count: 0
  };
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
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
    private readonly config: Pick<SidecarConfig, "browserName" | "allowFromMeInbound" | "fromMeMode" | "fromMeTestPrefix">
  ) {}

  async start(accountId: string): Promise<AccountSnapshot> {
    const account = this.account(accountId);
    if (account.socket && ["connected", "connecting", "qr_pending"].includes(account.status.status)) {
      return account.status;
    }
    account.status = { ...account.status, status: "connecting", last_error_code: null, last_error_message: null };
    await this.emitStatus(account);

    const { state, saveCreds } = await useMultiFileAuthState(this.sessions.accountPath(accountId));
    const { version } = await fetchLatestBaileysVersion();
    const socket = makeWASocket({
      version,
      auth: state,
      browser: Browsers.ubuntu(this.config.browserName),
      printQRInTerminal: false,
      logger: this.logger.child({ account_id: accountId }) as any
    });
    account.socket = socket;
    socket.ev.on("creds.update", saveCreds);
    socket.ev.on("connection.update", async (update) => {
      if (update.qr) {
        account.status = {
          ...account.status,
          status: "qr_pending",
          qr_status: "pending",
          qr: update.qr,
          qr_data_url: await qrDataUrl(update.qr),
          last_qr_generated_at: new Date().toISOString()
        };
        await this.emitStatus(account);
      }
      if (update.connection === "open") {
        account.pairingUntilMs = undefined;
        const jid = socket.user?.id || null;
        account.status = {
          ...account.status,
          status: "connected",
          qr_status: "consumed",
          qr: null,
          qr_data_url: null,
          jid,
          phone_number: jid ? `+${jid.split("@")[0].split(":")[0].replace(/\D/g, "")}` : null,
          last_connected_at: new Date().toISOString()
        };
        await this.emitStatus(account);
      }
      if (update.connection === "close") {
        const suppressReconnect = account.suppressReconnectFor === socket;
        if (suppressReconnect) {
          account.suppressReconnectFor = undefined;
        }
        if (account.socket !== socket) {
          return;
        }
        const statusCode = (update.lastDisconnect?.error as Boom | undefined)?.output?.statusCode;
        const loggedOut = statusCode === DisconnectReason.loggedOut;
        const pairingInProgress = this.isPairingInProgress(account);
        const disconnected = loggedOut && !pairingInProgress;
        account.status = {
          ...account.status,
          status: disconnected ? "disconnected" : "reconnecting",
          last_disconnected_at: new Date().toISOString(),
          last_error_code: statusCode ? String(statusCode) : "socket_closed",
          last_error_message: update.lastDisconnect?.error?.message || "socket closed",
          reconnect_count: account.status.reconnect_count + (disconnected ? 0 : 1)
        };
        await this.emitStatus(account);
        if (!disconnected && !suppressReconnect) {
          account.socket = undefined;
          void (async () => {
            if (pairingInProgress) {
              await sleep(PAIRING_CODE_RECONNECT_DELAY_MS);
            }
            await this.start(accountId);
          })().catch((error) => {
            this.logger.error({ account_id: accountId, error }, "whatsapp_reconnect_failed");
          });
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
    if (account.socket) {
      await account.socket.logout();
      account.socket = undefined;
    }
    account.pairingUntilMs = undefined;
    account.status = { ...baseSnapshot(accountId), status: "disconnected" };
    await this.emitStatus(account);
    return account.status;
  }

  async restart(accountId: string): Promise<AccountSnapshot> {
    const account = this.account(accountId);
    this.closeSocket(account, true);
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
    const result = await account.socket.sendMessage(jid, { text: request.body });
    return this.cacheSend(account, request.idempotency_key, {
      ok: true,
      status: "sent",
      provider_message_id: result?.key?.id || null,
      sent_at: new Date().toISOString()
    });
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

  private cacheSend(account: RuntimeAccount, key: string, result: SendResult): SendResult {
    account.idempotency.set(key, result);
    return result;
  }

  private async emitStatus(account: RuntimeAccount): Promise<void> {
    await this.onStatus(account.accountId, account.status);
  }
}
