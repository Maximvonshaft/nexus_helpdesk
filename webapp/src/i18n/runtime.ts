export type UiLocale = 'zh-CN' | 'en' | 'de'
export type UiMessageCatalog = Readonly<Record<string, string>>

const STORAGE_KEY = 'nexus-operator-ui-locale'

// PR1 deliberately enables only the existing locale. English and German become
// selectable only after their catalogs and full browser acceptance are complete.
export const enabledUiLocales = ['zh-CN'] as const satisfies readonly UiLocale[]

const catalogs: Readonly<Record<UiLocale, UiMessageCatalog>> = {
  'zh-CN': {},
  en: {},
  de: {},
}

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

function enabledLocale(value: UiLocale | null): UiLocale | null {
  return value && enabledUiLocales.includes(value as (typeof enabledUiLocales)[number])
    ? value
    : null
}

function initialLocale(): UiLocale {
  const stored = (() => {
    try {
      return enabledLocale(normalizeUiLocale(browserStorage()?.getItem(STORAGE_KEY)))
    } catch {
      return null
    }
  })()
  if (stored) return stored

  if (typeof navigator !== 'undefined') {
    for (const candidate of navigator.languages ?? [navigator.language]) {
      const locale = enabledLocale(normalizeUiLocale(candidate))
      if (locale) return locale
    }
  }
  return 'zh-CN'
}

let currentLocale: UiLocale = initialLocale()

export function getUiLocale(): UiLocale {
  return currentLocale
}

export function getIntlLocale(locale = currentLocale) {
  if (locale === 'de') return 'de-DE'
  if (locale === 'en') return 'en-GB'
  return 'zh-CN'
}

export function initializeUiLocale() {
  if (typeof document === 'undefined') return
  document.documentElement.lang = currentLocale
  document.documentElement.dataset.uiLocale = currentLocale
}

export function setUiLocale(locale: UiLocale) {
  const normalized = enabledLocale(normalizeUiLocale(locale))
  if (!normalized) throw new Error('ui_locale_not_enabled')
  try {
    browserStorage()?.setItem(STORAGE_KEY, normalized)
  } catch {
    // Storage can be unavailable in hardened or private browser contexts. The
    // current document still switches consistently through a controlled reload.
  }
  currentLocale = normalized
  initializeUiLocale()
  if (typeof window !== 'undefined') window.location.reload()
}

/**
 * Compile-time presentation literals call this function with an occurrence-
 * scoped key and the existing Chinese source. An absent catalog entry always
 * fails safely to the source, so technical or business-payload occurrences can
 * remain untranslated even when identical visible copy is localized elsewhere.
 */
export function translateStatic(key: string, source: string): string {
  const translated = catalogs[currentLocale][key]
  return typeof translated === 'string' && translated ? translated : source
}

export function translateTemplate(
  key: string,
  source: string,
  values: readonly unknown[],
): string {
  return translateStatic(key, source).replace(/\{\{(\d+)\}\}/g, (token, indexValue) => {
    const index = Number.parseInt(indexValue, 10)
    return Number.isInteger(index) && index < values.length
      ? String(values[index] ?? '')
      : token
  })
}
