export type ConnectorMode = "mock" | "baileys";
export type FromMeInboundMode = "ignore" | "store_only" | "test_visitor";

export type AccountStatus =
  | "idle"
  | "connecting"
  | "qr_pending"
  | "connected"
  | "disconnected"
  | "reconnecting"
  | "error";

export type QrStatus = "none" | "pending" | "consumed" | "expired";

export interface SidecarConfig {
  port: number;
  mode: ConnectorMode;
  sessionRoot: string;
  internalToken: string;
  backendUrl: string;
  connectorKey: string;
  connectorHmacSecret: string;
  callbackTimeoutMs: number;
  logLevel: string;
  allowFromMeInbound: boolean;
  fromMeMode: FromMeInboundMode;
  fromMeTestPrefix: string;
}

export interface AccountSnapshot {
  account_id: string;
  status: AccountStatus;
  qr_status: QrStatus;
  qr?: string | null;
  qr_data_url?: string | null;
  phone_number?: string | null;
  jid?: string | null;
  last_qr_generated_at?: string | null;
  last_connected_at?: string | null;
  last_disconnected_at?: string | null;
  last_error_code?: string | null;
  last_error_message?: string | null;
  reconnect_count: number;
}

export interface NormalizedInboundMessage {
  account_id: string;
  external_message_id: string;
  chat_jid: string;
  sender_jid: string;
  sender_phone: string | null;
  message_type: string;
  body_text: string;
  raw_payload: unknown;
  received_at: string;
  from_me?: boolean;
  projection_mode?: "visitor" | "store_only" | "test_visitor";
  self_echo_test_prefix?: string;
}

export interface SendRequest {
  idempotency_key: string;
  target?: string | null;
  chat_jid?: string | null;
  body: string;
  reply_to_message_id?: string | null;
  metadata?: Record<string, unknown>;
}

export interface SendResult {
  ok: boolean;
  status: "sent" | "failed";
  provider_message_id?: string | null;
  sent_at?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  retryable?: boolean;
}

export interface WhatsAppConnector {
  start(accountId: string): Promise<AccountSnapshot>;
  logout(accountId: string): Promise<AccountSnapshot>;
  restart(accountId: string): Promise<AccountSnapshot>;
  status(accountId: string): Promise<AccountSnapshot>;
  send(accountId: string, request: SendRequest): Promise<SendResult>;
}
