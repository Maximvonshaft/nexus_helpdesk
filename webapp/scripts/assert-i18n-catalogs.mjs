import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = process.cwd()
const inventoryPath = resolve(root, 'frontend_dist/i18n-inventory.json')
const catalogPaths = {
  en: resolve(root, 'public/i18n/en.json'),
  de: resolve(root, 'public/i18n/de.json'),
}
const cjk = /[\u3400-\u9fff]/u
const markerResidue = /[⟦⟧［］]|NXS\d+|ZX(?:PH|TERM)\d+ZX/iu
const repeatedGarbage = /([A-Za-z])\1{12,}/u
const placeholder = /\{\{\d+\}\}/gu

function readJson(path) {
  return JSON.parse(readFileSync(path, 'utf8'))
}

function placeholders(value) {
  return [...String(value).matchAll(placeholder)].map((match) => match[0]).sort()
}

function sameValues(left, right) {
  return left.length === right.length && left.every((value, index) => value === right[index])
}

const inventory = readJson(inventoryPath)
if (inventory.schema_version !== 2 || !Array.isArray(inventory.messages) || inventory.messages.length === 0) {
  throw new Error('i18n_catalog_inventory_invalid')
}

const expectedKeys = new Set(inventory.messages.map((message) => message.key))
if (expectedKeys.size !== inventory.messages.length) {
  throw new Error('i18n_catalog_inventory_duplicate_keys')
}

const report = {
  schema_version: 1,
  inventory_messages: inventory.messages.length,
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
    if (!sameValues(placeholders(message.source), placeholders(translated))) {
      failures.push([message.key, 'placeholder_mismatch'])
    }
    if (translated.length > Math.max(2400, String(message.source).length * 12)) {
      failures.push([message.key, 'length_explosion'])
    }
  }

  if (missing.length || extras.length || failures.length) {
    throw new Error(JSON.stringify({
      error: 'i18n_catalog_validation_failed',
      locale,
      missing: missing.slice(0, 20),
      extras: extras.slice(0, 20),
      failures: failures.slice(0, 40),
      counts: { missing: missing.length, extras: extras.length, failures: failures.length },
    }))
  }

  report.locales[locale] = {
    messages: keys.length,
    cjk_residue: 0,
    placeholder_failures: 0,
  }
}

writeFileSync(
  resolve(root, 'frontend_dist/i18n-catalog-report.json'),
  `${JSON.stringify(report, null, 2)}\n`,
  { encoding: 'utf8', mode: 0o600 },
)
console.log(JSON.stringify(report))
