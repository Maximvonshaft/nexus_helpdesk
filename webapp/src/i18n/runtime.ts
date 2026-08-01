import { createInstance } from 'i18next'
import type { Resource } from 'i18next'
import englishCatalog from './catalogs/en.json'
import germanCatalog from './catalogs/de.json'

export type UiLocale = 'zh-CN' | 'en' | 'de'
export type UiMessageCatalog = Readonly<Record<string, string>>

const STORAGE_KEY = 'nexus-operator-ui-locale'

export const enabledUiLocales = ['zh-CN', 'en', 'de'] as const satisfies readonly UiLocale[]

const catalogs: Readonly<Record<UiLocale, UiMessageCatalog>> = {
  'zh-CN': {},
  en: englishCatalog,
  de: germanCatalog,
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
  return value && enabledUiLocales.includes(value)
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

const resources: Resource = Object.fromEntries(
  enabledUiLocales.map((locale) => [locale, { translation: catalogs[locale] }]),
)

export const i18n = createInstance()
void i18n.init({
  resources,
  lng: currentLocale,
  supportedLngs: [...enabledUiLocales],
  fallbackLng: false,
  initImmediate: false,
  keySeparator: false,
  nsSeparator: false,
  returnEmptyString: false,
  returnNull: false,
  interpolation: {
    escapeValue: false,
    // Nexus templates deliberately use {{0}}, {{1}}, ... and perform bounded
    // positional substitution after catalog lookup. Distinct delimiters prevent
    // i18next from consuming those source-owned placeholders.
    prefix: '⟪',
    suffix: '⟫',
  },
})

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
  document.documentElement.dir = 'ltr'
  document.documentElement.dataset.uiLocale = currentLocale
}

export function persistUiLocale(value: unknown): { locale: UiLocale; changed: boolean } {
  const normalized = enabledLocale(normalizeUiLocale(value))
  if (!normalized) throw new Error('ui_locale_not_enabled')
  const changed = normalized !== currentLocale
  try {
    browserStorage()?.setItem(STORAGE_KEY, normalized)
  } catch {
    // Hardened/private browser contexts can deny storage. The active document
    // still receives the selected locale and the server remains authoritative
    // for the next authenticated session.
  }
  currentLocale = normalized
  void i18n.changeLanguage(normalized)
  initializeUiLocale()
  return { locale: normalized, changed }
}

export function setUiLocale(value: unknown, options?: { reload?: boolean }) {
  const result = persistUiLocale(value)
  if (result.changed && options?.reload !== false && typeof window !== 'undefined') {
    window.location.reload()
  }
  return result
}

/**
 * Reconcile the server-owned user preference after authentication. Returning a
 * boolean lets the caller perform one controlled navigation/reload without
 * introducing a second locale state authority.
 */
export function synchronizeAuthenticatedUiLocale(value: unknown): boolean {
  const normalized = enabledLocale(normalizeUiLocale(value))
  if (!normalized || normalized === currentLocale) return false
  persistUiLocale(normalized)
  return true
}

/**
 * Compile-time presentation literals call this function with an occurrence-
 * scoped key and the existing Chinese source. An absent catalog entry always
 * fails safely to the source, so technical or business-payload occurrences can
 * remain untranslated even when identical visible copy is localized elsewhere.
 */
export function translateStatic(key: string, source: string): string {
  const translated = i18n.t(key, { defaultValue: source })
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
