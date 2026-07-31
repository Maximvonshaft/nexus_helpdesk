import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'

const CJK_RE = /[\u3400-\u9fff]/u
const KEY_RE = /^[a-z0-9.]+\.[a-f0-9]{12}$/
const ALLOWED_KINDS = new Set([
  'jsx_attribute',
  'jsx_text',
  'static_literal',
  'template',
])

const inventoryPath = resolve(process.cwd(), '../frontend_dist/i18n-inventory.json')
const summaryPath = join(dirname(inventoryPath), 'i18n-inventory-summary.json')

const inventory = JSON.parse(readFileSync(inventoryPath, 'utf8'))
if (inventory?.schema_version !== 2 || !Array.isArray(inventory.messages)) {
  throw new Error('i18n_inventory_schema_invalid')
}
if (inventory.messages.length === 0) {
  throw new Error('i18n_inventory_empty')
}

const keys = new Set()
for (const message of inventory.messages) {
  if (!message || typeof message !== 'object') throw new Error('i18n_inventory_row_invalid')
  if (typeof message.key !== 'string' || !KEY_RE.test(message.key)) {
    throw new Error(`i18n_inventory_key_invalid:${String(message.key || '')}`)
  }
  if (keys.has(message.key)) throw new Error(`i18n_inventory_key_duplicate:${message.key}`)
  keys.add(message.key)
  if (typeof message.source !== 'string' || !CJK_RE.test(message.source)) {
    throw new Error(`i18n_inventory_source_invalid:${message.key}`)
  }
  if (!ALLOWED_KINDS.has(message.kind)) {
    throw new Error(`i18n_inventory_kind_invalid:${message.key}`)
  }
  if (!Array.isArray(message.occurrences) || message.occurrences.length === 0) {
    throw new Error(`i18n_inventory_occurrence_missing:${message.key}`)
  }
  for (const occurrence of message.occurrences) {
    if (
      typeof occurrence?.file !== 'string'
      || !occurrence.file.startsWith('src/')
      || !Number.isInteger(occurrence.line)
      || occurrence.line < 1
    ) {
      throw new Error(`i18n_inventory_occurrence_invalid:${message.key}`)
    }
  }
}

const summary = {
  ok: true,
  schema_version: inventory.schema_version,
  messages: inventory.messages.length,
  kinds: Object.fromEntries(
    [...ALLOWED_KINDS].map((kind) => [
      kind,
      inventory.messages.filter((message) => message.kind === kind).length,
    ]),
  ),
  inventory: inventoryPath,
}
writeFileSync(summaryPath, `${JSON.stringify(summary, null, 2)}\n`, {
  encoding: 'utf8',
  mode: 0o600,
})
process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`)
