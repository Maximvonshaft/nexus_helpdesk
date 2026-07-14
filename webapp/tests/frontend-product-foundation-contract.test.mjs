import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import test from 'node:test'

const webappRoot = resolve(process.cwd())
const repoRoot = resolve(webappRoot, '..')
const read = (path) => readFileSync(path, 'utf8')
const contract = JSON.parse(read(join(webappRoot, 'design', 'frontend-product-foundation.v1.json')))

test('customer-service product and design authorities exist', () => {
  for (const path of [
    join(webappRoot, 'PRODUCT.md'),
    join(webappRoot, 'DESIGN.md'),
    join(repoRoot, 'docs', 'engineering', 'frontend-product-foundation.md'),
    join(webappRoot, 'src', 'styles', 'tokens.css'),
    join(webappRoot, 'src', 'components', 'ui'),
  ]) assert.equal(existsSync(path), true, `missing frontend authority: ${path}`)
})

test('route contract exposes one customer-service product spine', () => {
  const routes = new Map(contract.route_domains.map((item) => [item.route, item]))
  for (const route of ['/login', '/workspace', '/knowledge', '/channels', '/system', '/webchat']) assert.ok(routes.has(route))
  assert.equal(routes.get('/workspace').canonical, true)
  assert.equal(routes.get('/workspace').status, 'current')
  assert.equal(routes.get('/webchat').canonical, false)
  assert.equal(routes.get('/webchat').status, 'redirect_only')
  assert.equal(routes.has('/runtime'), false)
})

test('single token and component authority is enforced', () => {
  assert.equal(contract.token_authority.semantic_tokens_path, 'webapp/src/styles/tokens.css')
  assert.equal(contract.token_authority.component_primitives_path, 'webapp/src/components/ui')
  assert.equal(contract.token_authority.enforcement_status, 'enforced')
  assert.equal(contract.token_authority.feature_raw_hex_policy, 'prohibited')
  assert.deepEqual(contract.token_authority.legacy_sources, [])
  assert.equal(contract.lifecycle.production_ui_migration_complete, true)
})

test('operator vocabulary is customer-service first and hides internal automation terms', () => {
  for (const value of ['AI', 'Runtime', 'Provider', 'RAG', 'Prompt', 'Model', 'Agent']) {
    assert.ok(contract.terminology.prohibited_operator_labels.includes(value), `missing prohibited label: ${value}`)
  }
  assert.ok(contract.terminology.preferred_evidence_labels.includes('已核实事实'))
  assert.ok(contract.terminology.preferred_evidence_labels.includes('客户最新说明'))
  assert.ok(contract.state_vocabulary.evidence.includes('evidence_historical_guidance'))
  assert.equal(contract.state_vocabulary.evidence.includes('evidence_ai_recommendation'), false)
})

test('product register defines the complete customer-service journey', () => {
  const product = read(join(webappRoot, 'PRODUCT.md'))
  for (const phrase of ['Who is the customer', 'Verified fact', 'Next action', 'Operational result', 'Customer notified', '/workspace']) {
    assert.match(product, new RegExp(phrase, 'i'))
  }
  assert.match(product, /Do not expose internal automation, model, provider, prompt, inference, or runtime terminology/)
})
