import type { Logger } from "pino";
import { Boom } from "@hapi/boom";
import makeWASocket, {
  DisconnectReason,
  fetchLatestBaileysVersion,
  useMultiFileAuthState,
  type WASocket
} from "@whiskeysockets/baileys";
import { normalizeBaileysInbound } from "./inboundMapper.js";
import { qrDataUrl } from "./qrManager.js";
import type { AccountSnapshot, NormalizedInboundMessage, SendRequest, SendResult, WhatsAppConnector } from "./types.js";
import { SessionStore } from "./sessionStore.js";

type InboundHandler = (message: NormalizedInboundMessage) => Promise<void>;
type StatusHandler = (accountId: string, snapshot: AccountSnapshot) => Promise<void>;

interface RuntimeAccount {
  accountId: string;
  socket?: WASocket;
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

export class BaileysConnector implements WhatsAppConnector {
  private readonly accounts = new Map<string, RuntimeAccount>();

  constructor(
    private readonly sessions: SessionStore,
    private readonly logger: Logger,
    private readonly onInbound: InboundHandler,
    private readonly onStatus: StatusHandler
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
        const statusCode = (update.lastDisconnect?.error as Boom | undefined)?.output?.statusCode;
        const loggedOut = statusCode === DisconnectReason.loggedOut;
        account.status = {
          ...account.status,
          status: loggedOut ? "disconnected" : "reconnecting",
          last_disconnected_at: new Date().toISOString(),
          last_error_code: statusCode ? String(statusCode) : "socket_closed",
          last_error_message: update.lastDisconnect?.error?.message || "socket closed",
          reconnect_count: account.status.reconnect_count + (loggedOut ? 0 : 1)
        };
        await this.emitStatus(account);
        if (!loggedOut) {
          account.socket = undefined;
          void this.start(accountId).catch((error) => {
            this.logger.error({ account_id: accountId, error }, "whatsapp_reconnect_failed");
          });
        }
      }
    });
    socket.ev.on("messages.upsert", async ({ messages }) => {
      for (const raw of messages || []) {
        const normalized = normalizeBaileysInbound(accountId, raw);
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
    account.status = { ...baseSnapshot(accountId), status: "disconnected" };
    await this.emitStatus(account);
    return account.status;
  }

  async restart(accountId: string): Promise<AccountSnapshot> {
    const account = this.account(accountId);
    if (account.socket) {
      account.socket.end(undefined);
      account.socket = undefined;
    }
    return this.start(accountId);
  }

  async status(accountId: string): Promise<AccountSnapshot> {
    return this.account(accountId).status;
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

  private targetToJid(target: string | null | undefined): string | null {
    const digits = (target || "").replace(/\D/g, "");
    return digits ? `${digits}@s.whatsapp.net` : null;
  }

  private cacheSend(account: RuntimeAccount, key: string, result: SendResult): SendResult {
    account.idempotency.set(key, result);
    return result;
  }

  private async emitStatus(account: RuntimeAccount): Promise<void> {
    await this.onStatus(account.accountId, account.status);
  }
}
