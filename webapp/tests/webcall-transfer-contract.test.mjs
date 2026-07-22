import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

const root = path.resolve(import.meta.dirname, '..')
const page = fs.readFileSync(
  path.join(root, 'src/features/webcall/WebCallPage.tsx'),
  'utf8',
)
const telephonyTypes = fs.readFileSync(
  path.join(root, 'src/lib/telephonyTypes.ts'),
  'utf8',
)


test('canonical WebCall exposes durable cold and warm transfer without a second console', () => {
  assert.match(page, /supportApi\.recordVoiceAction/)
  assert.match(page, /cold_transfer/)
  assert.match(page, /warm_transfer/)
  assert.match(page, /目标坐席、队列或电话号码/)
  assert.match(page, /直接转接/)
  assert.match(page, /咨询后转接/)
  assert.match(page, /!bootstrap/)
  assert.match(page, /等待 Provider 确认/)
})


test('WebCall binds transfer feedback to the canonical Voice Command contract', () => {
  assert.match(page, /VoiceCommandResponse/)
  assert.match(page, /result\?\.action\.id/)
  assert.match(telephonyTypes, /export interface VoiceCommandResponse/)
  assert.match(telephonyTypes, /voice_session_id: string/)
  assert.match(telephonyTypes, /action: VoiceCommandRead/)
  assert.match(telephonyTypes, /provider_status: string/)
  assert.match(telephonyTypes, /provider_reason\?: string \| null/)
})
