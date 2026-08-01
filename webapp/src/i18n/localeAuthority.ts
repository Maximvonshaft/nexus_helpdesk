export type UiLocale = 'zh-CN' | 'en' | 'de'
export type UiMessageCatalog = Readonly<Record<string, string>>

export interface UiI18nBootstrapState {
  locale: UiLocale
  catalog: UiMessageCatalog
  catalogLoaded: boolean
}

declare global {
  interface Window {
    __NEXUS_UI_I18N_BOOTSTRAP__?: UiI18nBootstrapState
  }
}

export const UI_LOCALE_STORAGE_KEY = 'nexus-operator-ui-locale'
export const UI_LOCALE_RECOVERY_KEY = 'nexus-operator-ui-locale-recovery'
export const enabledUiLocales = ['zh-CN', 'en', 'de'] as const satisfies readonly UiLocale[]

function localStorageAuthority() {
  return typeof window !== 'undefined' ? window.localStorage : null
}

function sessionStorageAuthority() {
  return typeof window !== 'undefined' ? window.sessionStorage : null
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

export function readRecoveryUiLocale(): UiLocale | null {
  try {
    return enabledUiLocale(sessionStorageAuthority()?.getItem(UI_LOCALE_RECOVERY_KEY))
  } catch {
    return null
  }
}

export function setRecoveryUiLocale(value: unknown): UiLocale {
  const normalized = enabledUiLocale(value)
  if (!normalized) throw new Error('ui_locale_recovery_not_enabled')
  try {
    sessionStorageAuthority()?.setItem(UI_LOCALE_RECOVERY_KEY, normalized)
  } catch {
    // Recovery remains best-effort in hardened/private browser contexts.
  }
  return normalized
}

export function clearRecoveryUiLocale() {
  try {
    sessionStorageAuthority()?.removeItem(UI_LOCALE_RECOVERY_KEY)
  } catch {
    // No-op: a normal account preference update remains authoritative.
  }
}

export function resolveInitialUiLocale(): UiLocale {
  const recovery = readRecoveryUiLocale()
  if (recovery) return recovery

  try {
    const stored = enabledUiLocale(localStorageAuthority()?.getItem(UI_LOCALE_STORAGE_KEY))
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
    localStorageAuthority()?.setItem(UI_LOCALE_STORAGE_KEY, normalized)
  } catch {
    // The server remains the authenticated authority when device storage fails.
  }
  clearRecoveryUiLocale()
  return normalized
}

export function intlLocale(locale: UiLocale) {
  if (locale === 'de') return 'de-DE'
  if (locale === 'en') return 'en-GB'
  return 'zh-CN'
}
