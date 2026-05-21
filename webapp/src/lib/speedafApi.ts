import { getToken, normalizeApiBaseUrl } from '@/lib/api'
import type {
  SpeedafActionResponse,
  SpeedafAddressUpdatePayload,
  SpeedafCancelPayload,
  SpeedafCancelPreviewPayload,
  SpeedafCancelPreviewResponse,
  SpeedafWorkOrderPayload,
} from '@/lib/speedafTypes'

const REQUEST_ID_HEADER = 'X-Request-Id'
const API_BASE_URL = normalizeApiBaseUrl(import.meta.env.VITE_API_BASE_URL)

function buildApiUrl(path: string) {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${API_BASE_URL}${normalizedPath}`
}

function createRequestId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

async function readErrorMessage(res: Response, fallback: string) {
  try {
    const data = await res.json()
    const detail = data?.detail
    return typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : JSON.stringify(data)
  } catch {
    return fallback
  }
}

async function speedafRequest<T>(path: string, payload: unknown): Promise<T> {
  const token = getToken()
  const headers = new Headers()
  headers.set('Content-Type', 'application/json')
  headers.set(REQUEST_ID_HEADER, createRequestId())
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const res = await fetch(buildApiUrl(path), {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    throw new Error(await readErrorMessage(res, `${res.status} ${res.statusText}`))
  }
  return res.json() as Promise<T>
}

export const speedafApi = {
  createWorkOrder: (ticketId: number, payload: SpeedafWorkOrderPayload) => (
    speedafRequest<SpeedafActionResponse>(`/api/tickets/${ticketId}/speedaf/work-orders`, payload)
  ),
  addressUpdate: (ticketId: number, payload: SpeedafAddressUpdatePayload) => (
    speedafRequest<SpeedafActionResponse>(`/api/tickets/${ticketId}/speedaf/address-update`, payload)
  ),
  cancelPreview: (ticketId: number, payload: SpeedafCancelPreviewPayload) => (
    speedafRequest<SpeedafCancelPreviewResponse>(`/api/tickets/${ticketId}/speedaf/cancel-preview`, payload)
  ),
  cancel: (ticketId: number, payload: SpeedafCancelPayload) => (
    speedafRequest<SpeedafActionResponse>(`/api/tickets/${ticketId}/speedaf/cancel`, payload)
  ),
}
