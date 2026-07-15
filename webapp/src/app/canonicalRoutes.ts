export type CanonicalAppRoute = '/workspace' | '/knowledge' | '/channels' | '/runtime' | '/control-tower'

const CANONICAL_ROUTES: CanonicalAppRoute[] = [
  '/workspace',
  '/knowledge',
  '/channels',
  '/runtime',
  '/control-tower',
]

export function canonicalAppHref(value: string | null | undefined): string | null {
  const candidate = String(value ?? '').trim()
  if (!candidate.startsWith('/')) return null

  for (const route of CANONICAL_ROUTES) {
    if (candidate === route || candidate.startsWith(`${route}?`) || candidate.startsWith(`${route}#`)) {
      return candidate
    }
  }
  return null
}
