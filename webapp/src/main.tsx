import catalogMetadata from '../design/i18n-production-catalog-metadata.json'
import { renderCatalogLoadFailure } from '@/i18n/catalogLoadFailure'
import { resolveInitialUiLocale } from '@/i18n/localeAuthority'
import type { UiLocale } from '@/i18n/localeAuthority'
import { staticJsonAssetRequest } from '@/lib/staticJsonAssetRequest'

type ExternalUiLocale = Exclude<UiLocale, 'zh-CN'>

function isCatalog(value: unknown): value is Record<string, string> {
  return Boolean(
    value
    && typeof value === 'object'
    && !Array.isArray(value)
    && Object.keys(value).length === catalogMetadata.inventory_messages
    && Object.values(value).every((entry) => typeof entry === 'string' && entry.trim().length > 0),
  )
}

async function loadCatalog(locale: UiLocale) {
  if (locale === 'zh-CN') return { catalog: {}, loaded: true }
  try {
    const catalogLocale = locale as ExternalUiLocale
    const catalogSha = catalogMetadata.catalog_sha256[catalogLocale]
    if (!catalogSha) throw new Error('catalog_digest_missing')
    const payload = await staticJsonAssetRequest<unknown>(
      `${import.meta.env.BASE_URL}i18n/${catalogLocale}.json?v=${encodeURIComponent(catalogSha)}`,
      { cache: 'no-cache', expectedSha256: catalogSha },
    )
    if (!isCatalog(payload)) throw new Error('catalog_shape_or_cardinality_invalid')
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
  await import('./application')
}
