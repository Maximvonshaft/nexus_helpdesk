export type ConnectorMode = "mock" | "baileys";
export type FromMeInboundMode = "ignore" | "store_only" | "test_visitor";
export type WhatsAppMediaKind = "image" | "video" | "audio" | "document" | "sticker";

export type AccountStatus =
  | "idle"
  | "connecting"
  | "qr_pending"
  | "auth_persisting"
  | "connected"
  | "disconnected"
  | "reconnecting"
  | "error";

export type AuthenticationState =
  | "unconfigured"
  | "pending"
  | "linked"
  | "unstable"
  | "revoked"
  | "error";

export type ListenerState =
  | "stopped"
  | "starting"
  | "active"
  | "reconnecting"
  | "error";

export type QrStatus = "none" | "pending" | "consumed" | "expired";

export interface SidecarConfig {
  port: number;
  mode: ConnectorMode;
  production: boolean;
  sessionRoot: string;
  callbackSpoolRoot: string;
  internalToken: string;
  backendUrl: string;
  connectorKey: string;
  connectorHmacSecret: string;
  callbackTimeoutMs: number;
  callbackRetryIntervalMs: number;
  reconcileIntervalMs: number;
  credentialPersistenceTimeoutMs: number;
  qrTtlMs: number;
  reconnectInitialMs: number;
  reconnectMaxMs: number;
  reconnectMaxAttempts: number;
  reconnectJitter: number;
  idempotencyTtlMs: number;
  logLevel: string;
  browserName: string;
  allowFromMeInbound: boolean;
  fromMeMode: FromMeInboundMode;
  fromMeTestPrefix: string;
}

export interface AccountSnapshot {
  account_id: string;
  status: AccountStatus;
  authentication_state: AuthenticationState;
  listener_state: ListenerState;
  qr_status: QrStatus;
  generation: number;
  qr?: string | null;
  qr_data_url?: string | null;
  qr_expires_at?: string | null;
  phone_number?: string | null;
  jid?: string | null;
  last_qr_generated_at?: string | null;
  last_connected_at?: string | null;
  last_disconnected_at?: string | null;
  last_inbound_at?: string | null;
  last_outbound_at?: string | null;
  last_transport_activity_at?: string | null;
  last_error_code?: string | null;
  last_error_message?: string | null;
  reconnect_count: number;
}

export interface DesiredAccount {
  account_id: string;
  generation: number;
}

export interface DesiredAccountResponse {
  ok: boolean;
  accounts: DesiredAccount[];
}

export interface NormalizedInboundMessage {
  transport: "baileys_sidecar";
  account_id: string;
  external_message_id: string;
  chat_jid: string;
  sender_jid: string;
  sender_phone: string | null;
  sender_name?: string | null;
  message_type: string;
  body_text: string;
  raw_message?: unknown;
  received_at: string;
  from_me?: boolean;
  projection_mode?: "visitor" | "store_only" | "test_visitor";
  self_echo_test_prefix?: string;
  reply_to_message_id?: string | null;
  media_id?: string | null;
  media_kind?: WhatsAppMediaKind | null;
  media_mime_type?: string | null;
  media_filename?: string | null;
}

export interface SendRequest {
  idempotency_key: string;
  target?: string | null;
  chat_jid?: string | null;
  body: string;
  reply_to_message_id?: string | null;
  metadata?: Record<string, unknown>;
}

export interface SendMediaRequest {
  idempotency_key: string;
  target?: string | null;
  chat_jid?: string | null;
  media_kind: WhatsAppMediaKind;
  media_type: string;
  filename?: string | null;
  caption?: string | null;
  metadata?: Record<string, unknown>;
}

export interface PairingCodeRequest {
  phone_number: string;
}

export interface PairingCodeResult {
  ok: boolean;
  account_id: string;
  pairing_code?: string | null;
  phone_number_suffix?: string | null;
  expires_at?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  retryable?: boolean;
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
  start(accountId: string, generation?: number): Promise<AccountSnapshot>;
  stop(accountId: string): Promise<AccountSnapshot>;
  logout(accountId: string): Promise<AccountSnapshot>;
  restart(accountId: string, generation?: number): Promise<AccountSnapshot>;
  status(accountId: string): Promise<AccountSnapshot>;
  requestPairingCode(
    accountId: string,
    request: PairingCodeRequest
  ): Promise<PairingCodeResult>;
  send(accountId: string, request: SendRequest): Promise<SendResult>;
  sendMedia(
    accountId: string,
    request: SendMediaRequest,
    content: Buffer
  ): Promise<SendResult>;
}
