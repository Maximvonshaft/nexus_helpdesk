import { renderCatalogLoadFailure } from '@/i18n/catalogLoadFailure'
import { resolveInitialUiLocale } from '@/i18n/localeAuthority'
import type { UiLocale } from '@/i18n/localeAuthority'

function isCatalog(value: unknown): value is Record<string, string> {
  return Boolean(
    value
    && typeof value === 'object'
    && !Array.isArray(value)
    && Object.values(value).every((entry) => typeof entry === 'string'),
  )
}

async function loadCatalog(locale: UiLocale) {
  if (locale === 'zh-CN') return { catalog: {}, loaded: true }
  try {
    const response = await fetch(`${import.meta.env.BASE_URL}i18n/${locale}.json`, {
      credentials: 'same-origin',
      cache: 'no-cache',
      headers: { Accept: 'application/json' },
    })
    if (!response.ok) throw new Error(`catalog_http_${response.status}`)
    const payload: unknown = await response.json()
    if (!isCatalog(payload)) throw new Error('catalog_shape_invalid')
    return { catalog: payload, loaded: true }
  } catch (error) {
    console.error('Nexus UI catalog failed to load; startup blocked.', error)
    return { catalog: {}, loaded: false }
  }
}

const locale = resolveInitialUiLocale()
const loaded = await loadCatalog(locale)
document.documentElement.lang = locale
document.documentElement.dir = 'ltr'
document.documentElement.dataset.uiLocale = locale
document.documentElement.dataset.uiCatalog = loaded.loaded ? 'loaded' : 'blocked'

if (!loaded.loaded && locale !== 'zh-CN') {
  renderCatalogLoadFailure(locale)
} else {
  window.__NEXUS_UI_I18N_BOOTSTRAP__ = {
    locale,
    catalog: loaded.catalog,
    catalogLoaded: loaded.loaded,
  }
  await import('./main')
}
