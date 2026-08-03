import { createHash } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const webappRoot = process.cwd()
const repositoryRoot = resolve(webappRoot, '..')
const inventoryPath = resolve(repositoryRoot, 'frontend_dist/i18n-inventory.json')
const criticalContractPath = resolve(webappRoot, 'design/i18n-critical-catalog.v1.json')
const provenancePath = resolve(webappRoot, 'design/i18n-production-catalog-metadata.json')
const catalogPaths = {
  en: resolve(webappRoot, 'public/i18n/en.json'),
  de: resolve(webappRoot, 'public/i18n/de.json'),
  cnr: resolve(webappRoot, 'public/i18n/cnr.json'),
}
const expectedModels = {
  en: {
    model_id: 'Helsinki-NLP/opus-mt-zh-en',
    requested_revision: 'cf109095479db38d6df799875e34039d4938aaa6',
    resolved_revision: 'cf109095479db38d6df799875e34039d4938aaa6',
    license: 'cc-by-4.0',
  },
  de: {
    model_id: 'Helsinki-NLP/opus-mt-en-de',
    requested_revision: '6183067f769a302e3861815543b9f312c71b0ca4',
    resolved_revision: '6183067f769a302e3861815543b9f312c71b0ca4',
    license: 'cc-by-4.0',
  },
  cnr: {
    model_id: 'Helsinki-NLP/opus-mt-en-zls',
    requested_revision: null,
    resolved_revision: 'f127a88f95d3550a38f6fe8075f91d0548220f3a',
    license: 'apache-2.0',
  },
}
const expectedBaseOverrideSha256 = 'ed6d9db7e85aeac51fda2c0babbbfa132d6fca430959b423d7e6a48fd3a42d9c'
const expectedCriticalOverrideSha256 = 'b46f127dea2291617c77cebc16a2a3eebe56bcecaebdc9e79a2df6b7bd6382b8'
const expectedBaseOverrideCounts = { en: 655, de: 655, cnr: 655 }
const expectedCriticalOverrideCounts = { en: 38, de: 38, cnr: 38 }
const cjk = /[\u3400-\u9fff]/u
const markerResidue = /[⟦⟧［］]|NXS\d+|ZX(?:PH|TERM)\d+ZX/iu
const repeatedGarbage = /([A-Za-z])\1{12,}/u
const formatToken = /\{\{\d+\}\}|%(?:\d+\$)?[sdif]|\{[A-Za-z_][A-Za-z0-9_]*\}/gu
const cnrForbiddenScriptOrMojibake = /[\u0400-\u052f]|\uFFFD|[èæœ]|b›/u

function readJson(path) {
  return JSON.parse(readFileSync(path, 'utf8'))
}

function sha256(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex')
}

function formatTokens(value) {
  return [...String(value).matchAll(formatToken)].map((match) => match[0]).sort()
}

function sameValues(left, right) {
  return left.length === right.length && left.every((value, index) => value === right[index])
}

function sameRecord(actual, expected) {
  return Object.keys(expected).every((key) => actual?.[key] === expected[key])
}

const inventory = readJson(inventoryPath)
if (inventory.schema_version !== 2 || !Array.isArray(inventory.messages) || inventory.messages.length === 0) {
  throw new Error('i18n_catalog_inventory_invalid')
}

const expectedKeys = new Set(inventory.messages.map((message) => message.key))
if (expectedKeys.size !== inventory.messages.length) {
  throw new Error('i18n_catalog_inventory_duplicate_keys')
}
const inventorySha256 = sha256(inventoryPath)
const uniqueSources = new Set(inventory.messages.map((message) => message.source)).size

const criticalContract = readJson(criticalContractPath)
if (
  criticalContract.schema !== 'nexus.i18n-critical-catalog.v1'
  || !criticalContract.messages
  || typeof criticalContract.messages !== 'object'
) {
  throw new Error('i18n_critical_catalog_contract_invalid')
}

const provenance = readJson(provenancePath)
const provenanceFailures = []
if (provenance.schema_version !== 1) provenanceFailures.push('schema_version')
if (provenance.policy !== 'production_candidate_requires_human_semantic_review_and_exact_head_acceptance') {
  provenanceFailures.push('policy')
}
if (provenance.inventory_messages !== inventory.messages.length) provenanceFailures.push('inventory_messages')
if (provenance.unique_sources !== uniqueSources) provenanceFailures.push('unique_sources')
if (provenance.inventory_sha256 !== inventorySha256) provenanceFailures.push('inventory_sha256')
if (provenance.base_override_sha256 !== expectedBaseOverrideSha256) provenanceFailures.push('base_override_sha256')
if (provenance.critical_override_sha256 !== expectedCriticalOverrideSha256) {
  provenanceFailures.push('critical_override_sha256')
}
if (!sameRecord(provenance.base_override_counts, expectedBaseOverrideCounts)) {
  provenanceFailures.push('base_override_counts')
}
if (!sameRecord(provenance.critical_override_counts, expectedCriticalOverrideCounts)) {
  provenanceFailures.push('critical_override_counts')
}
if (provenanceFailures.length > 0) {
  throw new Error(JSON.stringify({
    error: 'i18n_catalog_provenance_invalid',
    failures: provenanceFailures,
    expected_inventory: {
      messages: inventory.messages.length,
      unique_sources: uniqueSources,
      sha256: inventorySha256,
    },
    bound_inventory: {
      messages: provenance.inventory_messages,
      unique_sources: provenance.unique_sources,
      sha256: provenance.inventory_sha256,
    },
  }))
}
for (const [locale, expected] of Object.entries(expectedModels)) {
  if (!sameRecord(provenance.models?.[locale], expected)) {
    throw new Error(`i18n_catalog_model_provenance_invalid:${locale}`)
  }
  if (provenance.catalog_sha256?.[locale] !== sha256(catalogPaths[locale])) {
    throw new Error(`i18n_catalog_digest_mismatch:${locale}`)
  }
}

const inventoryBySource = new Map()
for (const message of inventory.messages) {
  const rows = inventoryBySource.get(message.source) ?? []
  rows.push(message)
  inventoryBySource.set(message.source, rows)
}

const report = {
  schema_version: 3,
  inventory_messages: inventory.messages.length,
  inventory_sha256: provenance.inventory_sha256,
  critical_sources: Object.keys(criticalContract.messages).length,
  base_override_sha256: provenance.base_override_sha256,
  critical_override_sha256: provenance.critical_override_sha256,
  locales: {},
}

for (const [locale, path] of Object.entries(catalogPaths)) {
  const catalog = readJson(path)
  const keys = Object.keys(catalog)
  const missing = [...expectedKeys].filter((key) => !(key in catalog))
  const extras = keys.filter((key) => !expectedKeys.has(key))
  const failures = []

  for (const message of inventory.messages) {
    const translated = catalog[message.key]
    if (typeof translated !== 'string' || !translated.trim()) {
      failures.push([message.key, 'empty'])
      continue
    }
    if (cjk.test(translated)) failures.push([message.key, 'cjk_residue'])
    if (markerResidue.test(translated)) failures.push([message.key, 'marker_residue'])
    if (repeatedGarbage.test(translated)) failures.push([message.key, 'repeated_garbage'])
    if (locale === 'cnr' && cnrForbiddenScriptOrMojibake.test(translated)) {
      failures.push([message.key, 'cnr_non_latin_or_mojibake'])
    }
    if (!sameValues(formatTokens(message.source), formatTokens(translated))) {
      failures.push([message.key, 'format_token_mismatch'])
    }
    if (translated.length > Math.max(2400, String(message.source).length * 12)) {
      failures.push([message.key, 'length_explosion'])
    }
  }

  for (const [source, reviewed] of Object.entries(criticalContract.messages)) {
    const occurrences = inventoryBySource.get(source) ?? []
    if (occurrences.length === 0) {
      failures.push([source, 'critical_source_missing_from_inventory'])
      continue
    }
    const expected = reviewed?.[locale]
    if (typeof expected !== 'string' || !expected) {
      failures.push([source, 'critical_locale_review_missing'])
      continue
    }
    for (const occurrence of occurrences) {
      if (catalog[occurrence.key] !== expected) {
        failures.push([occurrence.key, `critical_semantic_mismatch:${source}`])
      }
    }
  }

  if (missing.length || extras.length || failures.length) {
    throw new Error(JSON.stringify({
      error: 'i18n_catalog_validation_failed',
      locale,
      missing: missing.slice(0, 20),
      extras: extras.slice(0, 20),
      failures: failures.slice(0, 100),
      counts: { missing: missing.length, extras: extras.length, failures: failures.length },
    }))
  }

  report.locales[locale] = {
    messages: keys.length,
    catalog_sha256: provenance.catalog_sha256[locale],
    model_revision: provenance.models[locale].resolved_revision,
    license: provenance.models[locale].license,
    cjk_residue: 0,
    format_token_failures: 0,
    non_latin_or_mojibake_failures: 0,
    critical_semantic_failures: 0,
  }
}

writeFileSync(
  resolve(repositoryRoot, 'frontend_dist/i18n-catalog-report.json'),
  `${JSON.stringify(report, null, 2)}\n`,
  { encoding: 'utf8', mode: 0o600 },
)
console.log(JSON.stringify(report))
