import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')

const component = read('src/components/webcall/WebCallQueueFilters.tsx')
const agentPanel = read('src/components/webcall/AgentWebCallPanel.tsx')

test('WebCallQueueFilters uses source-level group/button semantics', () => {
  assert.match(component, /role="group"/)
  assert.match(component, /aria-label=\{ariaLabel\}/)
  assert.match(component, /WebCall Operational Queue filters/)
  assert.match(component, /aria-pressed=\{active\}/)
  assert.match(component, /onClick=\{\(\) => onSelect\(tab\.key\)\}/)
  assert.doesNotMatch(component, /role="tablist"/)
  assert.doesNotMatch(component, /role="tab"/)
  assert.doesNotMatch(component, /aria-selected/)
})

test('AgentWebCallPanel delegates queue filters to WebCallQueueFilters', () => {
  assert.match(agentPanel, /import \{ WebCallQueueFilters \} from '@\/components\/webcall\/WebCallQueueFilters'/)
  assert.match(agentPanel, /<WebCallQueueFilters\s+[\s\S]*tabs=\{QUEUE_TABS\}[\s\S]*activeKey=\{queueTab\}[\s\S]*onSelect=\{setQueueTab\}[\s\S]*\/>/)
  assert.doesNotMatch(agentPanel, /role="tablist"/)
  assert.doesNotMatch(agentPanel, /WebCall Operational Queue tabs/)
})
