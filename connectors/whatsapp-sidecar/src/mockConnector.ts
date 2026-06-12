import type { AccountSnapshot, SendRequest, SendResult, WhatsAppConnector } from "./types.js";

function snapshot(accountId: string, status: AccountSnapshot["status"]): AccountSnapshot {
  return {
    account_id: accountId,
    status,
    qr_status: status === "qr_pending" ? "pending" : "none",
    qr: status === "qr_pending" ? `mock-qr:${accountId}` : null,
    qr_data_url: null,
    phone_number: status === "connected" ? "+1000" : null,
    jid: status === "connected" ? "wa-mock" : null,
    last_qr_generated_at: status === "qr_pending" ? new Date().toISOString() : null,
    last_connected_at: status === "connected" ? new Date().toISOString() : null,
    last_disconnected_at: null,
    last_error_code: null,
    last_error_message: null,
    reconnect_count: 0
  };
}

export class MockConnector implements WhatsAppConnector {
  private readonly accounts = new Map<string, AccountSnapshot>();
  private readonly sends = new Map<string, SendResult>();

  async start(accountId: string): Promise<AccountSnapshot> {
    const state = snapshot(accountId, "qr_pending");
    this.accounts.set(accountId, state);
    return state;
  }

  async logout(accountId: string): Promise<AccountSnapshot> {
    const state = snapshot(accountId, "disconnected");
    this.accounts.set(accountId, state);
    return state;
  }

  async restart(accountId: string): Promise<AccountSnapshot> {
    return this.start(accountId);
  }

  async status(accountId: string): Promise<AccountSnapshot> {
    return this.accounts.get(accountId) || snapshot(accountId, "idle");
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
    const state = snapshot(accountId, "connected");
    this.accounts.set(accountId, state);
    return state;
  }
}
