export type UiLocale = 'zh-CN' | 'en' | 'de'

export const UI_LOCALE_STORAGE_KEY = 'nexus-operator-ui-locale'
export const enabledUiLocales = ['zh-CN', 'en', 'de'] as const satisfies readonly UiLocale[]

function browserStorage() {
  return typeof window !== 'undefined' ? window.localStorage : null
}

export function normalizeUiLocale(value: unknown): UiLocale | null {
  const candidate = String(value ?? '').trim().replaceAll('_', '-').toLowerCase()
  if (candidate === 'zh' || candidate === 'zh-cn' || candidate === 'zh-hans') return 'zh-CN'
  if (candidate === 'en' || candidate.startsWith('en-')) return 'en'
  if (candidate === 'de' || candidate.startsWith('de-')) return 'de'
  return null
}

export function enabledUiLocale(value: unknown): UiLocale | null {
  const normalized = normalizeUiLocale(value)
  return normalized && enabledUiLocales.includes(normalized) ? normalized : null
}

export function resolveInitialUiLocale(): UiLocale {
  try {
    const stored = enabledUiLocale(browserStorage()?.getItem(UI_LOCALE_STORAGE_KEY))
    if (stored) return stored
  } catch {
    // Browser storage is optional in hardened/private contexts.
  }

  if (typeof navigator !== 'undefined') {
    for (const candidate of navigator.languages ?? [navigator.language]) {
      const locale = enabledUiLocale(candidate)
      if (locale) return locale
    }
  }
  return 'zh-CN'
}

export function writeUiLocale(value: unknown): UiLocale {
  const normalized = enabledUiLocale(value)
  if (!normalized) throw new Error('ui_locale_not_enabled')
  try {
    browserStorage()?.setItem(UI_LOCALE_STORAGE_KEY, normalized)
  } catch {
    // The server remains the authenticated authority when device storage fails.
  }
  return normalized
}

export function intlLocale(locale: UiLocale) {
  if (locale === 'de') return 'de-DE'
  if (locale === 'en') return 'en-GB'
  return 'zh-CN'
}
