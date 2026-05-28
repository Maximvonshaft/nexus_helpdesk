import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(import.meta.dirname, '..')
const files = {
  publicWebcall: readFileSync(resolve(root, 'src/routes/webcall.tsx'), 'utf8'),
  agentPanel: readFileSync(resolve(root, 'src/components/webcall/AgentWebCallPanel.tsx'), 'utf8'),
  webcallAiProduction: readFileSync(resolve(root, 'src/routes/webcall-ai.tsx'), 'utf8'),
}

function countDynamicImports(source) {
  return [...source.matchAll(/await import\(['"]livekit-client['"]\)/g)].length
}

test('livekit-client is not imported at module top-level by WebCall entrypoints', () => {
  for (const [name, source] of Object.entries(files)) {
    assert.doesNotMatch(source, /from ['"]livekit-client['"]/, `${name} must not top-level import livekit-client`)
    assert.equal(countDynamicImports(source), 1, `${name} must have exactly one dynamic livekit import`)
  }
})

test('livekit media primitives remain inside explicit media action paths', () => {
  assert.match(files.publicWebcall, /async function joinCall\(\)/)
  assert.match(files.publicWebcall, /createLocalAudioTrack/)
  assert.match(files.publicWebcall, /new Room\(/)

  assert.match(files.agentPanel, /const acceptMutation = useMutation/)
  assert.match(files.agentPanel, /createLocalAudioTrack/)
  assert.match(files.agentPanel, /new Room\(/)

  assert.match(files.webcallAiProduction, /async function startCall\(\)/)
  assert.match(files.webcallAiProduction, /createLocalAudioTrack/)
  assert.match(files.webcallAiProduction, /new Room\(/)
})
