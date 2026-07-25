import { apiRequest, getSupportToken } from '@/lib/apiClient'

type WebVitalDetail = {
  name: 'LCP' | 'CLS' | 'INP'
  value: number
  rating: 'good' | 'needs-improvement' | 'poor'
}

type ApiLatencyDetail = {
  path: string
  method: string
  status: string
  duration_ms: number
}

type FrontendMetric =
  | {
      kind: 'web_vital'
      name: WebVitalDetail['name']
      rating: WebVitalDetail['rating']
      value: number
    }
  | {
      kind: 'api_latency'
      path: string
      method: string
      status: string
      duration_ms: number
    }

const ENDPOINT = '/api/observability/frontend-metrics'
const MAX_BATCH_SIZE = 50
const FLUSH_INTERVAL_MS = 5_000
let initialized = false
let inFlight = false
let queue: FrontendMetric[] = []
let flushTimer: number | undefined

function boundedNumber(value: unknown, maximum = 120_000) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return null
  return Math.max(0, Math.min(maximum, parsed))
}

function enqueue(metric: FrontendMetric) {
  if (metric.kind === 'web_vital') {
    queue = queue.filter((candidate) => candidate.kind !== 'web_vital' || candidate.name !== metric.name)
  }
  queue.push(metric)
  if (queue.length > MAX_BATCH_SIZE) queue = queue.slice(-MAX_BATCH_SIZE)
}

async function flush() {
  if (inFlight || !queue.length || !getSupportToken()) return
  const batch = queue.splice(0, MAX_BATCH_SIZE)
  inFlight = true
  try {
    await apiRequest<void>(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ metrics: batch }),
      keepalive: true,
      requestIdPrefix: 'frontend-metrics',
    })
  } catch {
    queue = [...batch, ...queue].slice(-MAX_BATCH_SIZE)
  } finally {
    inFlight = false
  }
}

function handleWebVital(event: Event) {
  const detail = (event as CustomEvent<WebVitalDetail>).detail
  if (!detail || !['LCP', 'CLS', 'INP'].includes(detail.name)) return
  const raw = boundedNumber(detail.value, detail.name === 'CLS' ? 10 : 120_000)
  if (raw === null) return
  enqueue({
    kind: 'web_vital',
    name: detail.name,
    rating: detail.rating,
    // Prometheus stores LCP/INP in seconds and CLS as its unitless score.
    value: detail.name === 'CLS' ? raw : raw / 1_000,
  })
}

function handleApiLatency(event: Event) {
  const detail = (event as CustomEvent<ApiLatencyDetail>).detail
  if (!detail || detail.path === ENDPOINT) return
  const duration = boundedNumber(detail.duration_ms)
  if (duration === null) return
  let pathname = detail.path
  try {
    pathname = new URL(detail.path, window.location.origin).pathname
  } catch {
    pathname = String(detail.path || '/').split(/[?#]/, 1)[0] || '/'
  }
  if (!pathname.startsWith('/')) return
  enqueue({
    kind: 'api_latency',
    path: pathname.slice(0, 240),
    method: String(detail.method || 'GET').toUpperCase().slice(0, 12),
    status: String(detail.status || 'unknown').slice(0, 32),
    duration_ms: duration,
  })
}

export function initFrontendTelemetry() {
  if (initialized || typeof window === 'undefined') return
  initialized = true
  window.addEventListener('nexusdesk:web-vital', handleWebVital)
  window.addEventListener('nexusdesk:api-latency', handleApiLatency)
  window.addEventListener('pagehide', () => { void flush() })
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') void flush()
  })
  flushTimer = window.setInterval(() => { void flush() }, FLUSH_INTERVAL_MS)
}

export function stopFrontendTelemetryForTest() {
  if (flushTimer !== undefined) window.clearInterval(flushTimer)
  flushTimer = undefined
  initialized = false
  queue = []
  inFlight = false
}
