import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8')

test('Agent control plane owns MCP Doctor, Run Explorer and one Fork workflow', () => {
  const page = read('src/features/agent-control/AgentControlPage.tsx')
  const diagnostics = read('src/features/agent-control/DiagnosticsPanel.tsx')
  const explorer = read('src/features/agent-control/RunExplorerPanel.tsx')
  const api = read('src/lib/agentRuntimeApi.ts')

  assert.match(page, /value="diagnostics"/)
  assert.match(page, /<DiagnosticsPanel/)
  assert.match(page, /<RunExplorerPanel/)
  assert.match(page, /scope=\{snapshot\.data\.scope\}/)

  assert.match(diagnostics, /运行 MCP Doctor/)
  assert.match(diagnostics, /未纳管 Tool/)
  assert.match(diagnostics, /不会自动进入 Agent/)

  assert.match(explorer, /Agent Run Explorer/)
  assert.match(explorer, /只读 Replay \/ Playground Fork/)
  assert.match(explorer, /value="replay"/)
  assert.match(explorer, /value="playground"/)
  assert.match(explorer, /onClick=\{\(\) => fork\.mutate\(\)\}/)
  assert.equal((explorer.match(/fork\.mutate\(/g) || []).length, 1)
  assert.match(explorer, /selectedRun\.status !== 'running'/)
  assert.match(explorer, /原不可变 Release/)
  assert.match(explorer, /写 Tool 永不进入 Fork/)

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
