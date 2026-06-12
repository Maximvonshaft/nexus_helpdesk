import type { Logger } from "pino";
import { BackendClient } from "./backendClient.js";
import { BaileysConnector } from "./baileysClient.js";
import { MockConnector } from "./mockConnector.js";
import { SessionStore } from "./sessionStore.js";
import type { AccountSnapshot, SendRequest, SendResult, SidecarConfig, WhatsAppConnector } from "./types.js";

export class AccountRegistry {
  readonly connector: WhatsAppConnector;

  constructor(
    private readonly config: SidecarConfig,
    private readonly logger: Logger,
    private readonly backend: BackendClient = new BackendClient(config, logger)
  ) {
    this.connector =
      config.mode === "baileys"
        ? new BaileysConnector(
            new SessionStore(config.sessionRoot),
            logger,
            async (message) => this.backend.postInbound(message),
            async (accountId, snapshot) => this.backend.postStatus(accountId, snapshot),
            config
          )
        : new MockConnector();
  }

  start(accountId: string): Promise<AccountSnapshot> {
    return this.connector.start(accountId);
  }

  logout(accountId: string): Promise<AccountSnapshot> {
    return this.connector.logout(accountId);
  }

  restart(accountId: string): Promise<AccountSnapshot> {
    return this.connector.restart(accountId);
  }

  status(accountId: string): Promise<AccountSnapshot> {
    return this.connector.status(accountId);
  }

  async qr(accountId: string): Promise<AccountSnapshot> {
    const state = await this.status(accountId);
    return {
      ...state,
      qr: state.qr_status === "pending" ? state.qr || null : null,
      qr_data_url: state.qr_status === "pending" ? state.qr_data_url || null : null
    };
  }

  async send(accountId: string, request: SendRequest): Promise<SendResult> {
    const result = await this.connector.send(accountId, request);
    await this.backend.postDelivery(accountId, {
      account_id: accountId,
      idempotency_key: request.idempotency_key,
      provider_message_id: result.provider_message_id || null,
      status: result.status,
      sent_at: result.sent_at || null,
      error_code: result.error_code || null,
      retryable: result.retryable ?? null,
      metadata: request.metadata || {}
    }).catch((error) => {
      this.logger.warn({ account_id: accountId, error }, "delivery_callback_failed");
    });
    return result;
  }
}
