const STORAGE_KEY = 'helpdesk-webapp-token'
const REQUEST_ID_HEADER = 'X-Request-Id'
const DEFAULT_API_TIMEOUT_MS = Number(import.meta.env.VITE_API_TIMEOUT_MS || 15000)
const PUBLIC_API_PATHS = ['/api/auth/login', '/auth/login', '/healthz', '/readyz']

export class AuthExpiredError extends Error {
  constructor(message = '登录状态已失效，请重新登录') {
    super(message)
    this.name = 'AuthExpiredError'
  }
}

export class ApiError extends Error {
  status: number
  detail?: unknown
  payload?: unknown

  constructor(message: string, status: number, detail?: unknown, payload?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.payload = payload
  }
}

export function normalizeApiBaseUrl(raw: string | undefined | null) {
  const trimmed = (raw ?? '').trim().replace(/\/+$/, '')
  return trimmed.replace(/\/api$/i, '')
}

const API_BASE_URL = normalizeApiBaseUrl(import.meta.env.VITE_API_BASE_URL)

function buildApiUrl(path: string) {
  if (/^https?:\/\//i.test(path)) return path
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${API_BASE_URL}${normalizedPath}`
}

function requestPathname(path: string) {
  if (!/^https?:\/\//i.test(path)) return path.startsWith('/') ? path : `/${path}`
  try {
    return new URL(path).pathname
  } catch {
    return path
  }
}

function isPublicRequest(path: string) {
  const pathname = requestPathname(path)
  return PUBLIC_API_PATHS.some((publicPath) => pathname === publicPath || pathname.endsWith(publicPath))
}

function createRequestId(prefix = 'req') {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

export function getSupportToken() {
  if (typeof sessionStorage === 'undefined') return null
  return sessionStorage.getItem(STORAGE_KEY)
}

export function setSupportToken(token: string | null) {
  if (typeof sessionStorage === 'undefined') return
  if (!token) sessionStorage.removeItem(STORAGE_KEY)
  else sessionStorage.setItem(STORAGE_KEY, token)
}

export function clearSupportToken() {
  if (typeof sessionStorage !== 'undefined') sessionStorage.removeItem(STORAGE_KEY)
}

function errorMessage(status: number, detail: unknown, fallback: string) {
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail === 'object') {
    const object = detail as { message?: unknown; error?: unknown; code?: unknown }
    for (const value of [object.message, object.error, object.code]) {
      if (typeof value === 'string' && value.trim()) return value
    }
  }
  if (status === 401) return '登录状态已失效，请重新登录'
  return fallback
}

async function readErrorBody(response: Response, fallback: string) {
  try {
    const data = await response.json() as { detail?: unknown; message?: unknown }
    const detail = data?.detail
    return {
      message: errorMessage(response.status, detail, typeof data?.message === 'string' ? data.message : fallback),
      detail,
      payload: data,
    }
  } catch {
    return { message: fallback, detail: undefined, payload: undefined }
  }
}

export type ApiRequestOptions = RequestInit & {
  timeoutMs?: number
  requestIdPrefix?: string
  requireApiPath?: boolean
}

export async function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const {
    timeoutMs = DEFAULT_API_TIMEOUT_MS,
    requestIdPrefix = 'req',
    requireApiPath = false,
    ...init
  } = options
  const normalizedPath = String(path || '').trim()
  if (requireApiPath && !normalizedPath.startsWith('/api/')) {
    throw new ApiError('服务器返回了不受支持的页面链接', 400)
  }

  const publicRequest = isPublicRequest(normalizedPath)
  const headers = new Headers(init.headers ?? {})
  const method = String(init.method || 'GET').toUpperCase()
  const token = getSupportToken()
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), Math.max(timeoutMs, 1000))

  if (!(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  headers.set(REQUEST_ID_HEADER, headers.get(REQUEST_ID_HEADER) || createRequestId(requestIdPrefix))
  if (token && !publicRequest) headers.set('Authorization', `Bearer ${token}`)

  try {
    const response = await fetch(buildApiUrl(normalizedPath), {
      ...init,
      method,
      headers,
      signal: init.signal ?? controller.signal,
    })
    if (response.status === 401 && !publicRequest) {
      clearSupportToken()
      throw new AuthExpiredError()
    }
    if (!response.ok) {
      const failure = await readErrorBody(response, `请求失败：${response.status}`)
      throw new ApiError(failure.message, response.status, failure.detail, failure.payload)
    }
    if (response.status === 204) return undefined as T
    return await response.json() as T
  } catch (error) {
    if (error instanceof ApiError || error instanceof AuthExpiredError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('请求超时，请稍后重试', 0)
    }
    throw new ApiError('网络请求失败，请稍后重试', 0)
  } finally {
    window.clearTimeout(timeout)
  }
}
