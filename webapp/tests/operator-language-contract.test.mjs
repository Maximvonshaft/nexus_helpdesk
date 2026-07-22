import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import test from 'node:test'

const webapp = resolve(process.cwd())
const repo = resolve(webapp, '..')
const register = JSON.parse(readFileSync(join(webapp, 'design/operator-language.v1.json'), 'utf8'))

function source(path) {
  return readFileSync(join(repo, path), 'utf8')
}

test('operator language register covers every canonical route domain', () => {
  assert.equal(register.schema, 'nexus.operator-language.v1')
  assert.equal(register.status, 'canonical_all_routes_enforced')
  for (const path of [
    'webapp/src/routes/workspace.tsx',
    'webapp/src/routes/knowledge.tsx',
    'webapp/src/routes/agent-control.tsx',
    'webapp/src/routes/channels.tsx',
    'webapp/src/routes/runtime.tsx',
    'webapp/src/routes/control-tower.tsx',
    'webapp/src/routes/administration.tsx',
    'webapp/src/routes/account.tsx',
  ]) {
    assert.ok(register.completed_surfaces.includes(path), path)
  }
})

test('all registered operator surfaces exist and contain no retired primary narration', () => {
  for (const path of register.completed_surfaces) {
    assert.equal(existsSync(join(repo, path)), true, path)
    const value = source(path)
    for (const literal of register.forbidden_primary_literals) {
      assert.equal(value.includes(literal), false, `${path}: ${literal}`)
    }
  }
})

test('task-oriented labels remain present on high-risk administration surfaces', () => {
  for (const [path, requiredLiterals] of Object.entries(register.required_surface_literals)) {
    const value = source(path)
    for (const literal of requiredLiterals) {
      assert.equal(value.includes(literal), true, `${path}: ${literal}`)
    }
  }
})

test('technical identifiers use named progressive disclosure on governed editors', () => {
  const requirements = {
    'webapp/src/features/agent-control/OverviewPanel.tsx': '技术详情',
    'webapp/src/features/agent-control/ToolsIntegrationsPanel.tsx': '连接详情',
    'webapp/src/features/agent-control/RunExplorerPanel.tsx': '运行详情',
    'webapp/src/features/administration/UserGovernance.tsx': '权限代码',
  }
  for (const [path, disclosure] of Object.entries(requirements)) {
    const value = source(path)
    assert.equal(value.includes('OperatorTechnicalDisclosure'), true, path)
    assert.equal(value.includes(disclosure), true, `${path}: ${disclosure}`)
  }
})

test('operator-facing permission failures do not expose raw policy codes', () => {
  const value = source('webapp/src/lib/apiErrorMap.ts')
  for (const literal of ['缺少 runtime.manage', '缺少 user.manage', '开通对应 capability', '确认 feature flag']) {
    assert.equal(value.includes(literal), false, literal)
  }
  assert.equal(value.includes('当前账号没有执行此操作的权限'), true)
})
