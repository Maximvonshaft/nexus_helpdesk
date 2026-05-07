type VitalRating = 'good' | 'needs-improvement' | 'poor'

type VitalMetric = {
  name: 'LCP' | 'CLS' | 'INP'
  value: number
  rating: VitalRating
  id: string
}

const DEBUG = String(import.meta.env.VITE_WEB_VITALS_DEBUG || '').toLowerCase() === 'true'
const ENABLED = String(import.meta.env.VITE_WEB_VITALS_ENABLED || 'false').toLowerCase() === 'true'

function ratingFor(name: VitalMetric['name'], value: number): VitalRating {
  if (name === 'LCP') {
    if (value <= 2500) return 'good'
    if (value <= 4000) return 'needs-improvement'
    return 'poor'
  }
  if (name === 'CLS') {
    if (value <= 0.1) return 'good'
    if (value <= 0.25) return 'needs-improvement'
    return 'poor'
  }
  if (value <= 200) return 'good'
  if (value <= 500) return 'needs-improvement'
  return 'poor'
}

function emit(metric: Omit<VitalMetric, 'rating' | 'id'>) {
  const payload: VitalMetric = {
    ...metric,
    rating: ratingFor(metric.name, metric.value),
    id: `${metric.name}-${Math.round(metric.value)}-${Date.now().toString(36)}`,
  }
  window.dispatchEvent(new CustomEvent('nexusdesk:web-vital', { detail: payload }))
  if (DEBUG) console.debug('[web-vital]', payload)
}

function observeLcp() {
  const PerformanceObserverCtor = window.PerformanceObserver
  if (!PerformanceObserverCtor) return
  try {
    const observer = new PerformanceObserverCtor((list) => {
      const entries = list.getEntries()
      const latest = entries.at(-1)
      if (latest) emit({ name: 'LCP', value: latest.startTime })
    })
    observer.observe({ type: 'largest-contentful-paint', buffered: true })
  } catch {
    // Browser does not support this entry type.
  }
}

function observeCls() {
  const PerformanceObserverCtor = window.PerformanceObserver
  if (!PerformanceObserverCtor) return
  let cls = 0
  try {
    const observer = new PerformanceObserverCtor((list) => {
      for (const entry of list.getEntries() as Array<PerformanceEntry & { hadRecentInput?: boolean; value?: number }>) {
        if (!entry.hadRecentInput) cls += entry.value || 0
      }
      emit({ name: 'CLS', value: cls })
    })
    observer.observe({ type: 'layout-shift', buffered: true })
  } catch {
    // Browser does not support this entry type.
  }
}

function observeInp() {
  const PerformanceObserverCtor = window.PerformanceObserver
  if (!PerformanceObserverCtor) return
  let maxInteraction = 0
  try {
    const observer = new PerformanceObserverCtor((list) => {
      for (const entry of list.getEntries() as Array<PerformanceEntry & { duration?: number }>) {
        maxInteraction = Math.max(maxInteraction, entry.duration || 0)
      }
      if (maxInteraction > 0) emit({ name: 'INP', value: maxInteraction })
    })
    observer.observe({ type: 'event', buffered: true, durationThreshold: 40 } as PerformanceObserverInit)
  } catch {
    // Browser does not support Event Timing / INP observation.
  }
}

export function initWebVitals() {
  if (!ENABLED && !DEBUG) return
  if (typeof window === 'undefined') return
  observeLcp()
  observeCls()
  observeInp()
}
