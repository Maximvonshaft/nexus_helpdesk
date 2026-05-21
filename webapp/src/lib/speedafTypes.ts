export interface SpeedafActionResponse {
  ok: boolean
  status: string
  message: string
  jobId?: number | null
  dedupeKey?: string | null
}

export interface SpeedafCancelPreviewResponse {
  ok: boolean
  cancelAllowed: boolean
  currentStatus?: string | null
  currentStatusLabel?: string | null
  reason?: string | null
  reasonLabel?: string | null
  confirmToken?: string | null
  expiresInSeconds?: number | null
}

export interface SpeedafWorkOrderPayload {
  waybillCode: string
  callerID: string
  workOrderType: string
  description: string
}

export interface SpeedafAddressUpdatePayload {
  waybillCode: string
  callerID: string
  whatsAppPhone: string
}

export interface SpeedafCancelPreviewPayload {
  waybillCode: string
  callerID: string
  reasonCode: string
}

export interface SpeedafCancelPayload extends SpeedafCancelPreviewPayload {
  confirmToken: string
}
