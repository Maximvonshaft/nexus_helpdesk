import { mapApiErrorMessage } from '@/lib/apiErrorMap'

const STORAGE_KEY = 'helpdesk-webapp-token'
const REQUEST_ID_HEADER = 'X-Request-Id'
const DEFAULT_API_TIMEOUT_MS = Number(import.meta.env.VITE_API_TIMEOUT_MS || 15000)
const PUBLIC_API_PATHS = [
  '/api/auth/login',
  '/auth/login',
  '/api/auth/register',
  '/auth/register',
  '/healthz',
  '/readyz',
  '/api/webchat/voice/runtime-config',
]
const SAFE_RETRY_METHODS = new Set(['GET', 'HEAD'])

let authExpiryHandled = false

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

function now() {
  return typeof performance !== 'undefined' ? performance.now() : Date.now()
}

function emitFrontendLatency(detail: {
  path: string
  method: string
  status: string
  duration_ms: number
  ok: boolean
  timeout?: boolean
}) {
  if (typeof window === 'undefined' || typeof CustomEvent === 'undefined') return
  window.dispatchEvent(new CustomEvent('nexusdesk:api-latency', { detail }))
}

export function getSupportToken() {
  if (typeof sessionStorage === 'undefined') return null
  return sessionStorage.getItem(STORAGE_KEY)
}

export function setSupportToken(token: string | null) {
  authExpiryHandled = false
  if (typeof sessionStorage === 'undefined') return
  if (!token) sessionStorage.removeItem(STORAGE_KEY)
  else sessionStorage.setItem(STORAGE_KEY, token)
}

export function clearSupportToken() {
  if (typeof sessionStorage !== 'undefined') sessionStorage.removeItem(STORAGE_KEY)
}

export const getToken = getSupportToken
export const setToken = setSupportToken
export const clearToken = clearSupportToken

async function readErrorBody(response: Response, fallback: string) {
  try {
    const data = await response.json() as { detail?: unknown; message?: unknown }
    const detail = data?.detail
    return {
      message: mapApiErrorMessage(
        response.status,
        detail,
        typeof data?.message === 'string' && data.message.trim() ? data.message : fallback,
      ),
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

async function fetchWithTimeout(url: string, init: RequestInit, timeoutMs: number) {
  const controller = new AbortController()
  const timeout = globalThis.setTimeout(() => controller.abort(), Math.max(timeoutMs, 1000))
  const externalSignal = init.signal
  const abortFromExternal = () => controller.abort()

  if (externalSignal?.aborted) controller.abort()
  else externalSignal?.addEventListener('abort', abortFromExternal, { once: true })

  try {
    return await fetch(url, { ...init, signal: controller.signal })
  } finally {
    globalThis.clearTimeout(timeout)
    externalSignal?.removeEventListener('abort', abortFromExternal)
  }
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
  const retryable = SAFE_RETRY_METHODS.has(method)
  const requestId = headers.get(REQUEST_ID_HEADER) || createRequestId(requestIdPrefix)
  const apiPath = requestPathname(normalizedPath)
  const url = buildApiUrl(normalizedPath)

  if (!(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  headers.set(REQUEST_ID_HEADER, requestId)
  const token = getSupportToken()
  if (token && !publicRequest) headers.set('Authorization', `Bearer ${token}`)

  let lastError: unknown
  for (let attempt = 0; attempt < (retryable ? 2 : 1); attempt += 1) {
    const started = now()
    try {
      const response = await fetchWithTimeout(url, { ...init, method, headers }, timeoutMs)
      const duration_ms = Math.round(now() - started)
      emitFrontendLatency({ path: apiPath, method, status: String(response.status), duration_ms, ok: response.ok })

      if (response.status === 401) {
        if (publicRequest) {
          const failure = await readErrorBody(response, '登录失败，请检查账号或密码')
          throw new ApiError(failure.message, response.status, failure.detail, failure.payload)
        }
        if (!authExpiryHandled) {
          authExpiryHandled = true
          clearSupportToken()
        }
        throw new AuthExpiredError()
      }
      if (!response.ok) {
        const failure = await readErrorBody(response, `请求失败：${response.status}`)
        throw new ApiError(failure.message, response.status, failure.detail, failure.payload)
      }
      if (response.status === 204) return undefined as T
      return await response.json() as T
    } catch (error) {
      lastError = error
      const timeout = error instanceof DOMException && error.name === 'AbortError'
      emitFrontendLatency({
        path: apiPath,
        method,
        status: timeout ? 'timeout' : 'network_error',
        duration_ms: Math.round(now() - started),
        ok: false,
        timeout,
      })
      if (!retryable || attempt > 0 || error instanceof AuthExpiredError || error instanceof ApiError) break
    }
  }

  if (lastError instanceof ApiError || lastError instanceof AuthExpiredError) throw lastError
  if (lastError instanceof DOMException && lastError.name === 'AbortError') {
    throw new ApiError('请求超时，请稍后重试', 0)
  }
  throw new ApiError('网络请求失败，请稍后重试', 0)
}
