import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const TEST_DIR = dirname(fileURLToPath(import.meta.url))
const WEBAPP_ROOT = resolve(TEST_DIR, '..')
const REPO_ROOT = resolve(WEBAPP_ROOT, '..')
const CONTRACT_PATH = join(WEBAPP_ROOT, 'design', 'operator-language.v1.json')

function readRepositoryPath(path) {
  return readFileSync(join(REPO_ROOT, path), 'utf8')
}

function contract() {
  assert.equal(existsSync(CONTRACT_PATH), true, 'operator language authority is missing')
  return JSON.parse(readFileSync(CONTRACT_PATH, 'utf8'))
}

test('operator language authority is versioned and bound to the sole UI delivery', () => {
  const value = contract()
  assert.equal(value.schema, 'nexus.operator-language.v1')
  assert.equal(value.work_item, 753)
  assert.equal(value.owner_pr, 754)
  assert.equal(value.status, 'implemented_except_workspace_pending_performance_convergence')
  assert.match(value.goal, /current task, current state, available action and recovery step/)
})

test('primary surfaces are limited to task, state, action and recovery language', () => {
  const value = contract()
  for (const responsibility of [
    'page or section name',
    'current state',
    'task fact',
    'field label',
    'action label',
    'blocking reason',
    'recovery instruction',
  ]) {
    assert.ok(value.primary_surface_rules.allowed.includes(responsibility), `missing allowed language role: ${responsibility}`)
  }
  for (const forbidden of [
    'product narration',
    'architecture explanation',
    'frontend or backend responsibility explanation',
    'permission philosophy',
    'AI self-description',
  ]) {
    assert.ok(value.primary_surface_rules.forbidden.includes(forbidden), `missing forbidden language class: ${forbidden}`)
  }
})

test('completed surfaces contain none of the retired narrative literals', () => {
  const value = contract()
  for (const path of value.completed_surfaces) {
    assert.equal(existsSync(join(REPO_ROOT, path)), true, `completed language surface is missing: ${path}`)
    const source = readRepositoryPath(path)
    for (const literal of value.forbidden_primary_literals) {
      assert.equal(source.includes(literal), false, `retired narrative literal returned in ${path}: ${literal}`)
    }
  }
})

test('operator-facing names replace internal platform vocabulary', () => {
  const runtime = readRepositoryPath('webapp/src/features/runtime/RuntimePage.tsx')
  const channels = readRepositoryPath('webapp/src/features/channels/ChannelsPage.tsx')
  const knowledge = readRepositoryPath('webapp/src/features/knowledge/KnowledgePage.tsx')
  const audit = readRepositoryPath('webapp/src/features/runtime/RuntimeEvidenceAudit.tsx')
  const controlTower = readRepositoryPath('webapp/src/features/control-tower/ControlTowerPage.tsx')

  assert.match(runtime, />系统状态</)
  assert.match(runtime, /服务提供方/)
  assert.doesNotMatch(runtime, />服务就绪状态<|>降级路径<|>Provider 诊断</)

  for (const label of ['账号名称', '接入位置', '绑定账号或号码', '外部账号编号', '系统信息']) {
    assert.ok(channels.includes(label), `channel operator label is missing: ${label}`)
  }
  assert.doesNotMatch(channels, /label="目标槽位"|label="期望绑定"|>Provider</)

  for (const label of ['标准答案与处理步骤', '回复方式', '搜索测试', '发布状态']) {
    assert.ok(knowledge.includes(label), `knowledge operator label is missing: ${label}`)
  }
  assert.doesNotMatch(knowledge, /测试命中|知识同步|答案事实与处理规则/)

  for (const label of ['处理记录', '查询与操作记录', '处理时间线', '问题记录', '审计数据', '查看原始数据']) {
    assert.ok(audit.includes(label), `audit operator label is missing: ${label}`)
  }
  assert.doesNotMatch(audit, /Turn #|Ticket #|Finding #|脱敏证据包|查看 JSON/)

  assert.match(controlTower, /系统与配置问题/)
  assert.match(controlTower, /去处理/)
  assert.doesNotMatch(controlTower, /运行与治理风险|后端未返回受支持的处理入口|打开处理页面/)
})

test('workspace copy remains one explicitly bounded pending convergence item', () => {
  const value = contract()
  assert.equal(value.pending_surfaces.length, 1)
  const pending = value.pending_surfaces[0]
  assert.equal(pending.path, 'webapp/src/features/operator-workspace/OperatorWorkspacePage.tsx')
  assert.match(pending.reason, /PR #759/)
  assert.equal(Object.keys(pending.required_replacements).length >= 10, true)
  assert.equal(pending.required_deletions.length >= 6, true)
  assert.equal(value.acceptance.some((item) => item.includes('integrated once after PR #759 convergence')), true)
})

test('technical identifiers are allowed only through named disclosures', () => {
  const value = contract()
  assert.deepEqual(value.technical_disclosure.allowed_locations, [
    '系统信息',
    '审计数据',
    '原始数据',
    '处理编号',
    '管理员-only pages',
  ])
  for (const rule of [
    'Provider is shown as 服务提供方.',
    'Ticket is shown as 工单.',
    'Job is shown as 处理编号.',
    'Finding is shown as 问题记录.',
  ]) {
    assert.ok(value.technical_disclosure.rules.includes(rule), `missing technical language rule: ${rule}`)
  }
})
