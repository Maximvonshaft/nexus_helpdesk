export type UiLocale = 'zh-CN' | 'en' | 'de' | 'cnr'
export type UiMessageCatalog = Readonly<Record<string, string>>
export type UiLocalePersistence = 'local' | 'session' | 'none'

export interface UiLocaleWriteResult {
  locale: UiLocale
  persistence: UiLocalePersistence
}

export interface UiI18nBootstrapState {
  locale: UiLocale
  catalog: UiMessageCatalog
  catalogLoaded: boolean
}

type UiLocaleRecoveryState = {
  locale: UiLocale
  userId: number | null
}

declare global {
  interface Window {
    __NEXUS_UI_I18N_BOOTSTRAP__?: UiI18nBootstrapState
  }
}

export const UI_LOCALE_STORAGE_KEY = 'nexus-operator-ui-locale'
export const UI_LOCALE_RECOVERY_KEY = 'nexus-operator-ui-locale-recovery'
export const UI_LOCALE_TRANSITION_KEY = 'nexus-operator-ui-locale-transition'
export const enabledUiLocales = ['zh-CN', 'en', 'de', 'cnr'] as const satisfies readonly UiLocale[]

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
  if (
    candidate === 'cnr'
    || candidate.startsWith('cnr-')
    || candidate === 'sr-me'
    || candidate === 'sr-latn-me'
  ) return 'cnr'
  return null
}

export function enabledUiLocale(value: unknown): UiLocale | null {
  const normalized = normalizeUiLocale(value)
  return normalized && enabledUiLocales.includes(normalized) ? normalized : null
}

function parseRecoveryState(raw: string | null): UiLocaleRecoveryState | null {
  if (!raw) return null

  // Accept the pre-scoping representation once so existing emergency sessions
  // can be safely claimed by the first authenticated account that resumes them.
  const legacyLocale = enabledUiLocale(raw)
  if (legacyLocale) return { locale: legacyLocale, userId: null }

  try {
    const value = JSON.parse(raw) as { locale?: unknown; userId?: unknown }
    const locale = enabledUiLocale(value.locale)
    const userId = value.userId === null ? null : Number(value.userId)
    if (!locale || (userId !== null && (!Number.isInteger(userId) || userId <= 0))) return null
    return { locale, userId }
  } catch {
    return null
  }
}

function readRecoveryState(): UiLocaleRecoveryState | null {
  try {
    return parseRecoveryState(sessionStorageAuthority()?.getItem(UI_LOCALE_RECOVERY_KEY) ?? null)
  } catch {
    return null
  }
}

function writeRecoveryState(state: UiLocaleRecoveryState): boolean {
  try {
    const storage = sessionStorageAuthority()
    if (!storage) return false
    const serialized = JSON.stringify(state)
    storage.setItem(UI_LOCALE_RECOVERY_KEY, serialized)
    const verified = parseRecoveryState(storage.getItem(UI_LOCALE_RECOVERY_KEY))
    return verified?.locale === state.locale && verified.userId === state.userId
  } catch {
    return false
  }
}

export function readRecoveryUiLocale(): UiLocale | null {
  return readRecoveryState()?.locale ?? null
}

export function setRecoveryUiLocale(value: unknown): boolean {
  const normalized = enabledUiLocale(value)
  if (!normalized) throw new Error('ui_locale_recovery_not_enabled')
  return writeRecoveryState({ locale: normalized, userId: null })
}

/**
 * Bind an emergency recovery choice to the authenticated account that resumes
 * it. A different account never inherits the previous operator's override.
 */
export function claimRecoveryUiLocale(userIdValue: unknown): UiLocale | null {
  const userId = Number(userIdValue)
  if (!Number.isInteger(userId) || userId <= 0) return null

  const state = readRecoveryState()
  if (!state) return null
  if (state.userId === userId) return state.locale
  if (state.userId !== null) {
    clearRecoveryUiLocale()
    return null
  }

  return writeRecoveryState({ ...state, userId }) ? state.locale : null
}

export function clearRecoveryUiLocale() {
  try {
    sessionStorageAuthority()?.removeItem(UI_LOCALE_RECOVERY_KEY)
  } catch {
    // No-op: a normal account preference update remains authoritative.
  }
}

export function stageUiLocaleTransition(value: unknown): boolean {
  const normalized = enabledUiLocale(value)
  if (!normalized) throw new Error('ui_locale_transition_not_enabled')
  try {
    const storage = sessionStorageAuthority()
    if (!storage) return false
    storage.setItem(UI_LOCALE_TRANSITION_KEY, normalized)
    return storage.getItem(UI_LOCALE_TRANSITION_KEY) === normalized
  } catch {
    return false
  }
}

function consumeUiLocaleTransition(): UiLocale | null {
  try {
    const storage = sessionStorageAuthority()
    if (!storage) return null
    const transition = enabledUiLocale(storage.getItem(UI_LOCALE_TRANSITION_KEY))
    storage.removeItem(UI_LOCALE_TRANSITION_KEY)
    return transition
  } catch {
    return null
  }
}

function readStoredUiLocale(): UiLocale | null {
  // A tab-scoped value exists only when durable storage was unavailable. It is
  // therefore the stronger authority for that tab and must be checked first.
  try {
    const sessionLocale = enabledUiLocale(sessionStorageAuthority()?.getItem(UI_LOCALE_STORAGE_KEY))
    if (sessionLocale) return sessionLocale
  } catch {
    // Continue to durable storage below.
  }

  try {
    return enabledUiLocale(localStorageAuthority()?.getItem(UI_LOCALE_STORAGE_KEY))
  } catch {
    return null
  }
}

export function resolveInitialUiLocale(): UiLocale {
  const recovery = readRecoveryUiLocale()
  if (recovery) return recovery

  // A user-initiated language change stages a one-document transition marker
  // before reload. Consuming it first prevents a stale/external same-origin
  // write from bouncing the application back into an adoption reload loop.
  const transition = consumeUiLocaleTransition()
  if (transition) {
    // Reassert the target after consuming the one-shot marker so the document
    // state and the strongest available persistent authority converge again.
    writeUiLocale(transition)
    return transition
  }

  return resolvePreferredUiLocale()
}

/** Resolve durable, tab-scoped or browser locale without emergency recovery. */
export function resolvePreferredUiLocale(): UiLocale {
  const stored = readStoredUiLocale()
  if (stored) return stored

  if (typeof navigator !== 'undefined') {
    for (const candidate of navigator.languages ?? [navigator.language]) {
      const locale = enabledUiLocale(candidate)
      if (locale) return locale
    }
  }
  return 'zh-CN'
}

export function writeUiLocale(value: unknown): UiLocaleWriteResult {
  const normalized = enabledUiLocale(value)
  if (!normalized) throw new Error('ui_locale_not_enabled')

  let persistence: UiLocalePersistence = 'none'
  try {
    const storage = localStorageAuthority()
    if (storage) {
      storage.setItem(UI_LOCALE_STORAGE_KEY, normalized)
      if (storage.getItem(UI_LOCALE_STORAGE_KEY) === normalized) {
        persistence = 'local'
        try {
          sessionStorageAuthority()?.removeItem(UI_LOCALE_STORAGE_KEY)
        } catch {
          // A stale session fallback cannot override the verified local value.
        }
      }
    }
  } catch {
    // Continue to tab-scoped persistence when durable storage is blocked.
  }

  if (persistence === 'none') {
    try {
      const storage = sessionStorageAuthority()
      if (storage) {
        storage.setItem(UI_LOCALE_STORAGE_KEY, normalized)
        if (storage.getItem(UI_LOCALE_STORAGE_KEY) === normalized) persistence = 'session'
      }
    } catch {
      // Callers must not reload when neither storage authority can retain locale.
    }
  }

  clearRecoveryUiLocale()
  return { locale: normalized, persistence }
}

export function intlLocale(locale: UiLocale) {
  if (locale === 'de') return 'de-DE'
  if (locale === 'en') return 'en-GB'
  if (locale === 'cnr') return 'sr-Latn-ME'
  return 'zh-CN'
}
