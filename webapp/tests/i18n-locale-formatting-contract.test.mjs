import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const read = (path) => readFileSync(resolve(ROOT, path), 'utf8')
const timestampFiles = [
  'src/features/agent-control/ReleaseDeliveryPanel.tsx',
  'src/features/agent-control/RunExplorerPanel.tsx',
  'src/features/agent-control/PersonaPanel.tsx',
]
const metricFiles = [
  'src/features/agent-control/ReleaseDeliveryPanel.tsx',
  'src/features/agent-control/RunExplorerPanel.tsx',
  'src/features/control-tower/ControlTowerPage.tsx',
  'src/features/runtime/RuntimePage.tsx',
  'src/features/knowledge/KnowledgeImportPanel.tsx',
  'src/features/knowledge/KnowledgePage.tsx',
]

test('operator timestamps use the canonical UI locale formatter', () => {
  for (const path of timestampFiles) {
    const source = read(path)
    assert.doesNotMatch(source, /\.toLocaleString\(\)/, path)
    assert.match(source, /formatDateTime/, path)
  }
})

test('operator metrics use canonical locale-aware number formatters', () => {
  for (const path of metricFiles) {
    const source = read(path)
    assert.doesNotMatch(source, /\.toFixed\(/, path)
    assert.match(source, /formatNumber|formatPercent/, path)
  }
})

test('signed KPI deltas and formatted percentage labels preserve semantics', () => {
  const controlTower = read('src/features/control-tower/ControlTowerPage.tsx')
  const releaseDelivery = read('src/features/agent-control/ReleaseDeliveryPanel.tsx')
  assert.equal((controlTower.match(/item\.delta >= 0 \? '\+' : '-'/g) ?? []).length, 2)
  assert.match(releaseDelivery, /formatPercent\(\(delivery\.data\?\.deployment\.canary_percent \?\? selected\.canary_percent\) \/ 100, 0\)/)

  const key = 'features.agent.control.releasedeliverypanel.4e563bbd0f64'
  const expected = {
    en: 'Effective share {{0}}',
    de: 'Aktiver Anteil {{0}}',
    cnr: 'Aktivni udio {{0}}',
  }
  for (const [locale, value] of Object.entries(expected)) {
    const catalog = JSON.parse(read(`public/i18n/${locale}.json`))
    assert.equal(catalog[key], value)
    assert.doesNotMatch(catalog[key], /\b(?:per cent|percent|Prozent|procenat)\b/i)
  }
})
