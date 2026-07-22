import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8')

test('automatic handling owns connection diagnostics, run records and one historical test workflow', () => {
  const page = read('src/features/agent-control/AgentControlPage.tsx')
  const diagnostics = read('src/features/agent-control/DiagnosticsPanel.tsx')
  const explorer = read('src/features/agent-control/RunExplorerPanel.tsx')
  const api = read('src/lib/agentRuntimeApi.ts')

  assert.match(page, /value="diagnostics"/)
  assert.match(page, /<DiagnosticsPanel/)
  assert.match(page, /<RunExplorerPanel/)
  assert.match(page, /scope=\{snapshot\.data\.scope\}/)

  assert.match(diagnostics, /检查连接/)
  assert.match(diagnostics, /未纳入配置/)
  assert.match(diagnostics, /系统不会调用这些工具/)
  assert.match(diagnostics, /agentRuntimeApi\.doctorMcp/)

  assert.match(explorer, /运行记录/)
  assert.match(explorer, /基于此记录测试/)
  assert.match(explorer, /value="replay"/)
  assert.match(explorer, /value="playground"/)
  assert.match(explorer, /onClick=\{\(\) => testRun\.mutate\(\)\}/)
  assert.equal((explorer.match(/testRun\.mutate\(/g) || []).length, 1)
  assert.match(explorer, /selectedRun\.status !== 'running'/)
  assert.match(explorer, /fork_kind: testKind/)
  assert.match(explorer, /execute_model: generateReply/)
  assert.match(explorer, /OperatorTechnicalDisclosure title="运行详情"/)

  assert.match(api, /doctorMcp:/)
  assert.match(api, /runs:/)
  assert.match(api, /runEvents:/)
  assert.match(api, /forkRun:/)
  assert.match(api, /\/runs\/\$\{runId\}\/fork/)
})

test('Agent Runtime evidence UI does not expose forbidden payload names', () => {
  const explorer = read('src/features/agent-control/RunExplorerPanel.tsx')
  const diagnostics = read('src/features/agent-control/DiagnosticsPanel.tsx')

  for (const forbidden of [
    'chain of thought',
    'hidden reasoning',
    'raw_prompt',
    'raw_tool_arguments',
    'provider_raw_payload',
  ]) {
    assert.equal(explorer.toLowerCase().includes(forbidden), false)
    assert.equal(diagnostics.toLowerCase().includes(forbidden), false)
  }
})
