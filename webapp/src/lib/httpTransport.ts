export const DEFAULT_HTTP_TIMEOUT_MS = Number(import.meta.env.VITE_API_TIMEOUT_MS || 15000)

/**
 * The single native fetch lifecycle for the operator web application.
 * Higher-level modules own URL policy, authentication, retries and response
 * interpretation; this module owns abort propagation and bounded timeouts only.
 */
export async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<Response> {
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
