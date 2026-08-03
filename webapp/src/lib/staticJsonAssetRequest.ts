import { DEFAULT_HTTP_TIMEOUT_MS, fetchWithTimeout } from './httpTransport'

export type StaticJsonAssetOptions = {
  timeoutMs?: number
  cache?: RequestCache
  expectedSha256?: string
}

async function sha256Hex(bytes: ArrayBuffer) {
  const subtle = globalThis.crypto?.subtle
  if (!subtle) throw new Error('static_asset_digest_unavailable')
  const digest = await subtle.digest('SHA-256', bytes)
  return [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, '0'))
    .join('')
}

/**
 * Loads same-origin immutable application assets before the translation runtime
 * is imported. The module deliberately has no dependency on apiClient, the
 * i18n runtime or operator-facing translated error maps.
 */
export async function staticJsonAssetRequest<T>(
  path: string,
  options: StaticJsonAssetOptions = {},
): Promise<T> {
  const normalizedPath = String(path || '').trim()
  if (!normalizedPath) throw new Error('static_asset_path_required')

  const url = new URL(normalizedPath, window.location.origin)
  if (url.origin !== window.location.origin) throw new Error('static_asset_cross_origin_forbidden')

  const response = await fetchWithTimeout(
    url.toString(),
    {
      method: 'GET',
      credentials: 'same-origin',
      cache: options.cache ?? 'no-cache',
      headers: { Accept: 'application/json' },
    },
    options.timeoutMs ?? DEFAULT_HTTP_TIMEOUT_MS,
  )
  if (!response.ok) throw new Error(`static_asset_http_${response.status}`)

  const bytes = await response.arrayBuffer()
  if (options.expectedSha256 !== undefined) {
    const expected = String(options.expectedSha256).trim().toLowerCase()
    if (!/^[0-9a-f]{64}$/.test(expected)) throw new Error('static_asset_digest_invalid')
    const actual = await sha256Hex(bytes)
    if (actual !== expected) throw new Error('static_asset_digest_mismatch')
  }

  const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  return JSON.parse(text) as T
}
