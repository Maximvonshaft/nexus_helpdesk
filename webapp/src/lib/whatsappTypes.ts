export type WhatsAppTransport = 'baileys_sidecar' | 'meta_cloud_api'
export type WhatsAppDesiredState = 'disabled' | 'active'

export type WhatsAppConnection = {
  id: number
  tenant_id: number
  channel_account_id: number
  account_id: string
  display_name: string | null
  market_id: number | null
  priority: number
  channel_active: boolean
  transport: WhatsAppTransport
  desired_state: 'disabled' | 'binding' | 'active'
  observed_state: string
  authentication_state: string
  listener_state: string
  verification_state: string
  desired_generation: number
  observed_generation: number
  phone_number_mask: string | null
  business_account_id: string | null
  waba_id: string | null
  phone_number_id: string | null
  graph_api_version: string | null
  sidecar_session_key: string | null
  session_generation: number
  access_token_configured: boolean
  app_secret_configured: boolean
  verify_token_configured: boolean
  last_qr_generated_at: string | null
  qr_expires_at: string | null
  last_connected_at: string | null
  last_disconnected_at: string | null
  last_inbound_at: string | null
  last_outbound_at: string | null
  last_probe_at: string | null
  last_probe_status: string | null
  reconnect_count: number
  last_error_code: string | null
  last_error_message: string | null
  inbound_tested_at: string | null
  outbound_tested_at: string | null
  verified_at: string | null
  created_at: string
  updated_at: string
}

export type WhatsAppConnectionCreate = {
  display_name: string
  account_id: string
  market_id?: number | null
  priority?: number
  transport: WhatsAppTransport
  sidecar_session_key?: string | null
  business_account_id?: string | null
  waba_id?: string | null
  phone_number_id?: string | null
  graph_api_version?: string | null
  access_token?: string | null
  app_secret?: string | null
  verify_token?: string | null
}

export type WhatsAppConnectionUpdate = Partial<Omit<WhatsAppConnectionCreate, 'account_id' | 'transport'>>

export type WhatsAppBindingStatus = {
  connection_id: number
  channel_account_id: number
  transport: WhatsAppTransport
  observed_state: string
  authentication_state: string
  listener_state: string
  verification_state: string
  desired_generation: number
  observed_generation: number
  qr_status: string | null
  qr_data_url: string | null
  qr_expires_at: string | null
  phone_number_mask: string | null
  last_connected_at: string | null
  last_disconnected_at: string | null
  last_inbound_at: string | null
  last_outbound_at: string | null
  last_probe_at: string | null
  reconnect_count: number
  last_error_code: string | null
  last_error_message: string | null
}

export type WhatsAppPairingCode = {
  pairing_code: string
  phone_number_suffix: string
  expires_at: string | null
}

export type WhatsAppTestResult = {
  ok: boolean
  connection_id: number
  transport: WhatsAppTransport
  provider_message_id: string | null
  verification_state: string
  occurred_at: string
}

export type EmbeddedSignupIntent = {
  display_name: string
  account_id: string
  market_id?: number | null
  priority?: number
}

export type EmbeddedSignupSession = {
  session_id: string
  state: string
  expires_at: string
  app_id: string
  configuration_id: string
  graph_api_version: string
  allowed_origin: string
}

export type EmbeddedSignupFinish = {
  business_account_id?: string | null
  waba_id: string
  phone_number_id: string
}

export type EmbeddedSignupCompletion = {
  state: string
  code: string
  business_account_id?: string | null
  waba_id: string
  phone_number_id: string
  display_name: string
  account_id: string
  market_id?: number | null
  priority?: number
}

export type EmbeddedSignupCompleteResult = {
  ok: boolean
  session_id: string
  connection_id: number
  account_id: string
  waba_id: string
  phone_number_id: string
  desired_state: string
  verification_state: string
  binding_state: 'started' | 'attention_required'
  binding_error_code: string | null
  binding_retryable: boolean
  idempotent: boolean
}
