import type { Logger } from "pino";
import { BackendClient } from "./backendClient.js";
import { BaileysConnector } from "./baileysClient.js";
import { MockConnector } from "./mockConnector.js";
import { SessionStore } from "./sessionStore.js";
import type {
  AccountSnapshot,
  DesiredAccount,
  PairingCodeRequest,
  PairingCodeResult,
  SendMediaRequest,
  SendRequest,
  SendResult,
  SidecarConfig,
  WhatsAppConnector
} from "./types.js";

export interface RegistryReadiness {
  ready: boolean;
  status: "ready" | "starting" | "degraded" | "not_ready";
  authority_established: boolean;
  authority_fresh: boolean;
  last_authority_success_at: string | null;
  last_authority_failure_at: string | null;
  last_authority_error_code: string | null;
  desired_accounts: number;
  pending_callbacks: number;
}

type ConnectorWithPendingMedia = WhatsAppConnector & {
  pendingMediaDownloads?: () => number;
};

export class AccountRegistry {
  readonly connector: WhatsAppConnector;
  private readonly desiredAccounts = new Map<string, number>();
  private lastAuthoritySuccessMs: number | null = null;
  private lastAuthorityFailureMs: number | null = null;
  private lastAuthorityErrorCode: string | null = null;

  constructor(
    private readonly config: SidecarConfig,
    private readonly logger: Logger,
    readonly backend: BackendClient = new BackendClient(config, logger)
  ) {
    this.connector =
      config.mode === "baileys"
        ? new BaileysConnector(
            new SessionStore(config.sessionRoot),
            logger,
            async (message) => this.backend.postInbound(message),
            async (media) => this.backend.postMedia(media),
            async (accountId, snapshot) => this.backend.postStatus(accountId, snapshot),
            config,
            async (accountId, payload) => this.backend.postDelivery(accountId, payload)
          )
        : new MockConnector();
  }

  start(accountId: string, generation?: number): Promise<AccountSnapshot> {
    if (generation !== undefined) this.desiredAccounts.set(accountId, generation);
    return this.connector.start(accountId, generation);
  }

  stop(accountId: string): Promise<AccountSnapshot> {
    this.desiredAccounts.delete(accountId);
    return this.connector.stop(accountId);
  }

  logout(accountId: string): Promise<AccountSnapshot> {
    this.desiredAccounts.delete(accountId);
    return this.connector.logout(accountId);
  }

  restart(accountId: string, generation?: number): Promise<AccountSnapshot> {
    if (generation !== undefined) this.desiredAccounts.set(accountId, generation);
    return this.connector.restart(accountId, generation);
  }

  status(accountId: string): Promise<AccountSnapshot> {
    return this.connector.status(accountId);
  }

  async qr(accountId: string): Promise<AccountSnapshot> {
    const state = await this.status(accountId);
    const pending = state.qr_status === "pending" && Boolean(state.qr_expires_at);
    return {
      ...state,
      qr: pending ? state.qr || null : null,
      qr_data_url: pending ? state.qr_data_url || null : null
    };
  }

  requestPairingCode(accountId: string, request: PairingCodeRequest): Promise<PairingCodeResult> {
    return this.connector.requestPairingCode(accountId, request);
  }

  async send(accountId: string, request: SendRequest): Promise<SendResult> {
    const result = await this.connector.send(accountId, request);
    await this.postDelivery(accountId, request, result);
    return result;
  }

  async sendMedia(
    accountId: string,
    request: SendMediaRequest,
    content: Buffer
  ): Promise<SendResult> {
    const result = await this.connector.sendMedia(accountId, request, content);
    await this.postDelivery(accountId, request, result);
    return result;
  }

  async reconcile(accounts: DesiredAccount[]): Promise<void> {
    const next = new Map(
      accounts.map((account) => [account.account_id, Math.max(0, account.generation)])
    );
    for (const [accountId, generation] of next) {
      const currentGeneration = this.desiredAccounts.get(accountId);
      if (currentGeneration === generation) {
        const snapshot = await this.status(accountId);
        if (["connected", "connecting", "qr_pending", "reconnecting"].includes(snapshot.status)) {
          continue;
        }
      }
      this.desiredAccounts.set(accountId, generation);
      await this.start(accountId, generation).catch((error) => {
        this.logger.error(
          { account_id: accountId, generation, error },
          "whatsapp_desired_account_start_failed"
        );
      });
    }
    for (const accountId of [...this.desiredAccounts.keys()]) {
      if (next.has(accountId)) continue;
      await this.connector.stop(accountId).catch((error) => {
        this.logger.warn({ account_id: accountId, error }, "whatsapp_desired_account_stop_failed");
      });
      this.desiredAccounts.delete(accountId);
    }
  }

  recordAuthoritySuccess(observedAt = Date.now()): void {
    this.lastAuthoritySuccessMs = observedAt;
    this.lastAuthorityErrorCode = null;
  }

  recordAuthorityFailure(error: unknown, observedAt = Date.now()): void {
    this.lastAuthorityFailureMs = observedAt;
    this.lastAuthorityErrorCode = publicAuthorityErrorCode(error);
  }

  readiness(now = Date.now()): RegistryReadiness {
    const established = this.lastAuthoritySuccessMs !== null;
    const staleAfterMs = Math.max(this.config.reconcileIntervalMs * 3, 30_000);
    const fresh = established && now - Number(this.lastAuthoritySuccessMs) <= staleAfterMs;
    const currentlyFailing =
      this.lastAuthorityFailureMs !== null &&
      (this.lastAuthoritySuccessMs === null || this.lastAuthorityFailureMs > this.lastAuthoritySuccessMs);
    const ready = Boolean(fresh);
    const status: RegistryReadiness["status"] = !established
      ? "starting"
      : ready && currentlyFailing
        ? "degraded"
        : ready
          ? "ready"
          : "not_ready";
    const pendingMediaDownloads =
      (this.connector as ConnectorWithPendingMedia).pendingMediaDownloads?.() ?? 0;
    return {
      ready,
      status,
      authority_established: established,
      authority_fresh: Boolean(fresh),
      last_authority_success_at: iso(this.lastAuthoritySuccessMs),
      last_authority_failure_at: iso(this.lastAuthorityFailureMs),
      last_authority_error_code: this.lastAuthorityErrorCode,
      desired_accounts: this.desiredAccounts.size,
      pending_callbacks: this.backend.pendingCallbacks() + pendingMediaDownloads
    };
  }

  desiredAccountCount(): number {
    return this.desiredAccounts.size;
  }

  private async postDelivery(
    accountId: string,
    request: SendRequest | SendMediaRequest,
    result: SendResult
  ): Promise<void> {
    await this.backend.postDelivery(accountId, {
      account_id: accountId,
      idempotency_key: request.idempotency_key,
      provider_message_id: result.provider_message_id || null,
      status: result.status,
      sent_at: result.sent_at || null,
      error_code: result.error_code || null,
      error_message: result.error_message || null,
      retryable: result.retryable ?? null,
      metadata: request.metadata || {}
    });
  }
}

function iso(value: number | null): string | null {
  return value === null ? null : new Date(value).toISOString();
}

function publicAuthorityErrorCode(error: unknown): string {
  const message = error instanceof Error ? error.message : "";
  if (message.startsWith("backend_callback_failed:")) return "backend_authority_http_error";
  if (message === "invalid_desired_account_response") return message;
  if (error instanceof DOMException && error.name === "AbortError") {
    return "backend_authority_timeout";
  }
  return "backend_authority_unavailable";
}
