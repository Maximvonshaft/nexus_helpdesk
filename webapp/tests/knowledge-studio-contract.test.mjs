import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(import.meta.dirname, '..')
const route = readFileSync(resolve(root, 'src/routes/knowledge-studio.tsx'), 'utf8')
const router = readFileSync(resolve(root, 'src/router.tsx'), 'utf8')
const appShell = readFileSync(resolve(root, 'src/layouts/AppShell.tsx'), 'utf8')
const commandPalette = readFileSync(resolve(root, 'src/components/ui/CommandPalette.tsx'), 'utf8')
const rbac = readFileSync(resolve(root, 'src/lib/rbac.ts'), 'utf8')
const api = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')
const types = readFileSync(resolve(root, 'src/lib/types.ts'), 'utf8')

test('top-level knowledge studio route is registered and routeAccess gated', () => {
  assert.match(route, /path: '\/knowledge-studio'/)
  assert.match(route, /beforeLoad: \(\) => \{ if \(!getToken\(\)\) throw redirect\(\{ to: '\/login' \}\) \}/)
  assert.match(route, /<RequireCapability requirement=\{routeAccess\['\/knowledge-studio'\]\}>/)
  assert.match(router, /KnowledgeStudioRoute/)
  assert.match(router, /@\/routes\/knowledge-studio/)
  assert.match(appShell, /to: '\/knowledge-studio'[\s\S]*label: 'Knowledge Studio'[\s\S]*access: routeAccess\['\/knowledge-studio'\]/)
  assert.match(commandPalette, /id: 'knowledge-studio'[\s\S]*to: '\/knowledge-studio'[\s\S]*access: routeAccess\['\/knowledge-studio'\]/)
  assert.match(rbac, /'\/knowledge-studio': \{ anyOf: \[CAPABILITIES\.aiConfigRead, CAPABILITIES\.aiConfigManage\] \}/)
})

test('knowledge studio uses unified client for real backend knowledge contracts', () => {
  for (const apiCall of [
    'api.knowledgeItems',
    'api.knowledgeItem',
    'api.createKnowledgeItem',
    'api.updateKnowledgeItem',
    'api.createKnowledgeItemFromUpload',
    'api.uploadKnowledgeDocument',
    'api.publishKnowledgeItem',
    'api.rollbackKnowledgeItem',
    'api.testKnowledgeRetrieval',
    'api.testKnowledgeRuntimeContext',
  ]) {
    assert.match(route, new RegExp(apiCall.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  }
  assert.match(api, /testKnowledgeRuntimeContext/)
  assert.match(api, /\/api\/knowledge-items\/runtime-context-test/)
  assert.match(types, /export interface KnowledgeRuntimeContextTestResult/)
  assert.doesNotMatch(route, /\bfetch\s*\(/)
})

test('knowledge studio keeps read access separate from write capability', () => {
  assert.match(route, /canReadAIConfig\(session\.data\)/)
  assert.match(route, /canManageAIConfig\(session\.data\)/)
  assert.match(route, /当前账号只有查看权限/)
  assert.match(route, /disabled=\{!canManage \|\| saveKnowledge\.isPending\}/)
  assert.match(route, /disabled=\{!canManage \|\| !uploadFile \|\| uploadKnowledge\.isPending\}/)
  assert.match(route, /disabled=\{!canManage \|\| !selectedKnowledgeId \|\| publishKnowledge\.isPending\}/)
})

test('knowledge studio covers template workflow shape without mock data', () => {
  assert.match(route, /data-testid="knowledge-studio-item-list"/)
  assert.match(route, /data-testid="knowledge-studio-editor"/)
  assert.match(route, /data-testid="knowledge-draft-chunk-preview"/)
  assert.match(route, /data-testid="knowledge-golden-question"/)
  assert.match(route, /data-testid="knowledge-release-evidence"/)
  assert.match(route, /Golden Question \/ Runtime 测试/)
  assert.match(route, /运行时上下文证据/)
  assert.match(route, /发布并索引/)
  assert.match(route, /回滚并重新发布/)
  assert.doesNotMatch(route, /mock|fixture|demo knowledge/i)
})
