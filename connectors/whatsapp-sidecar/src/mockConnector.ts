import type {
  AccountSnapshot,
  PairingCodeRequest,
  PairingCodeResult,
  SendRequest,
  SendResult,
  WhatsAppConnector
} from "./types.js";

function snapshot(
  accountId: string,
  status: AccountSnapshot["status"],
  generation = 0
): AccountSnapshot {
  const connected = status === "connected";
  const pending = status === "qr_pending";
  return {
    account_id: accountId,
    status,
    authentication_state: connected ? "linked" : pending ? "pending" : "unconfigured",
    listener_state: connected ? "active" : pending ? "starting" : "stopped",
    qr_status: pending ? "pending" : connected ? "consumed" : "none",
    generation,
    qr: pending ? `mock-qr:${accountId}` : null,
    qr_data_url: pending ? `data:image/png;base64,mock-${accountId}` : null,
    qr_expires_at: pending ? new Date(Date.now() + 60000).toISOString() : null,
    phone_number: connected ? "+1000" : null,
    jid: connected ? "1000@s.whatsapp.net" : null,
    last_qr_generated_at: pending ? new Date().toISOString() : null,
    last_connected_at: connected ? new Date().toISOString() : null,
    last_disconnected_at: null,
    last_inbound_at: null,
    last_outbound_at: null,
    last_transport_activity_at: connected ? new Date().toISOString() : null,
    last_error_code: null,
    last_error_message: null,
    reconnect_count: 0
  };
}

export class MockConnector implements WhatsAppConnector {
  private readonly accounts = new Map<string, AccountSnapshot>();
  private readonly sends = new Map<string, SendResult>();

  async start(accountId: string, generation = 0): Promise<AccountSnapshot> {
    const state = snapshot(accountId, "qr_pending", generation);
    this.accounts.set(accountId, state);
    return state;
  }

  async stop(accountId: string): Promise<AccountSnapshot> {
    const generation = this.accounts.get(accountId)?.generation ?? 0;
    const state = snapshot(accountId, "idle", generation);
    this.accounts.set(accountId, state);
    return state;
  }

  async logout(accountId: string): Promise<AccountSnapshot> {
    const generation = this.accounts.get(accountId)?.generation ?? 0;
    const state = {
      ...snapshot(accountId, "disconnected", generation),
      authentication_state: "revoked" as const
    };
    this.accounts.set(accountId, state);
    return state;
  }

  async restart(accountId: string, generation?: number): Promise<AccountSnapshot> {
    return this.start(accountId, generation ?? this.accounts.get(accountId)?.generation ?? 0);
  }

  async status(accountId: string): Promise<AccountSnapshot> {
    return this.accounts.get(accountId) || snapshot(accountId, "idle");
  }

  async requestPairingCode(
    accountId: string,
    request: PairingCodeRequest
  ): Promise<PairingCodeResult> {
    const digits = request.phone_number.replace(/\D/g, "");
    const generation = this.accounts.get(accountId)?.generation ?? 0;
    this.accounts.set(accountId, snapshot(accountId, "connecting", generation));
    return {
      ok: true,
      account_id: accountId,
      pairing_code: "12345678",
      phone_number_suffix: digits.slice(-4) || null,
      expires_at: new Date(Date.now() + 180000).toISOString()
    };
  }

  async send(accountId: string, request: SendRequest): Promise<SendResult> {
    const existing = this.sends.get(request.idempotency_key);
    if (existing) return existing;
    const state = this.accounts.get(accountId);
    if (state?.status !== "connected") {
      const failed: SendResult = {
        ok: false,
        status: "failed",
        error_code: "whatsapp_not_connected",
        retryable: true
      };
      this.sends.set(request.idempotency_key, failed);
      return failed;
    }
    const sent: SendResult = {
      ok: true,
      status: "sent",
      provider_message_id: `mock-${request.idempotency_key}`,
      sent_at: new Date().toISOString()
    };
    this.sends.set(request.idempotency_key, sent);
    return sent;
  }

  setConnected(accountId: string): AccountSnapshot {
    const generation = this.accounts.get(accountId)?.generation ?? 0;
    const state = snapshot(accountId, "connected", generation);
    this.accounts.set(accountId, state);
    return state;
  }
}
