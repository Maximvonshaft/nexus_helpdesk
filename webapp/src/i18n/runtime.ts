import { createInstance } from 'i18next'
import type { Resource } from 'i18next'
import {
  enabledUiLocale,
  enabledUiLocales,
  intlLocale,
  readRecoveryUiLocale,
  resolveInitialUiLocale,
  writeUiLocale,
} from './localeAuthority'
import type { UiI18nBootstrapState, UiLocale, UiMessageCatalog } from './localeAuthority'

export type { UiLocale, UiMessageCatalog } from './localeAuthority'
export { enabledUiLocales, normalizeUiLocale } from './localeAuthority'

function bootstrapState(): UiI18nBootstrapState {
  if (typeof window !== 'undefined' && window.__NEXUS_UI_I18N_BOOTSTRAP__) {
    return window.__NEXUS_UI_I18N_BOOTSTRAP__
  }
  const locale = resolveInitialUiLocale()
  return { locale, catalog: {}, catalogLoaded: locale === 'zh-CN' }
}

const bootstrap = bootstrapState()
let currentLocale: UiLocale = bootstrap.locale
let currentCatalog: UiMessageCatalog = bootstrap.catalog
let currentCatalogLoaded = bootstrap.catalogLoaded

const resources: Resource = {
  [currentLocale]: { translation: currentCatalog },
}

export const i18n = createInstance()
void i18n.init({
  resources,
  lng: currentLocale,
  supportedLngs: [...enabledUiLocales],
  fallbackLng: false,
  initAsync: false,
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
  return intlLocale(locale)
}

export function initializeUiLocale() {
  if (typeof document === 'undefined') return
  document.documentElement.lang = currentLocale
  document.documentElement.dir = 'ltr'
  document.documentElement.dataset.uiLocale = currentLocale
  document.documentElement.dataset.uiCatalog = currentCatalogLoaded ? 'loaded' : 'fallback'
}

export function persistUiLocale(value: unknown): { locale: UiLocale; changed: boolean } {
  const normalized = enabledUiLocale(value)
  if (!normalized) throw new Error('ui_locale_not_enabled')
  const changed = normalized !== currentLocale
  writeUiLocale(normalized)
  currentLocale = normalized
  currentCatalog = {}
  currentCatalogLoaded = normalized === 'zh-CN'
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
 * Reconcile the server-owned user preference after authentication. A temporary
 * session recovery locale deliberately suspends this projection so an operator
 * can enter Account settings after a catalog outage and repair the persisted
 * preference without a reload loop.
 */
export function synchronizeAuthenticatedUiLocale(value: unknown): boolean {
  if (readRecoveryUiLocale()) return false
  const normalized = enabledUiLocale(value)
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
