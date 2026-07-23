import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

const root = path.resolve(import.meta.dirname, '..')
const page = fs.readFileSync(
  path.join(root, 'src/features/webcall/WebCallPage.tsx'),
  'utf8',
)
const telephonyApi = fs.readFileSync(
  path.join(root, 'src/lib/telephonyApi.ts'),
  'utf8',
)
const telephonyTypes = fs.readFileSync(
  path.join(root, 'src/lib/telephonyTypes.ts'),
  'utf8',
)


test('canonical WebCall owns cold transfer and explicit warm consultation phases', () => {
  assert.match(page, /telephonyApi\.recordCommand/)
  assert.match(page, /cold_transfer/)
  assert.match(page, /warm_transfer/)
  assert.match(page, /warm_transfer_complete/)
  assert.match(page, /warm_transfer_cancel/)
  assert.match(page, /目标坐席、队列或电话号码/)
  assert.match(page, /直接转接/)
  assert.match(page, /开始咨询/)
  assert.match(page, /完成转接/)
  assert.match(page, /取消咨询/)
  assert.match(page, /咨询线路建立不等于转接完成/)
  assert.match(page, /客户目前处于保持状态/)
  assert.match(page, /!bootstrap/)
})


test('warm consultation never reports completion from the start command', () => {
  assert.match(page, /providerPhase\(result\) !== 'consulting'/)
  assert.match(page, /setConsulting\(true\)/)
  assert.match(page, /客户仍保持中/)
  assert.match(page, /phase !== 'completed'/)
  assert.match(page, /phase !== 'cancelled'/)
  assert.match(page, /Provider 已确认咨询转接完成/)
  assert.match(page, /咨询已取消，客户通话已恢复/)
  assert.doesNotMatch(page, /Provider 已确认咨询转接完成['"]\s*:\s*['"]Provider 已确认咨询转接完成/)
})


test('WebCall reports success only after the canonical Provider command reaches a terminal result', () => {
  assert.match(page, /waitForCommand/)
  assert.match(page, /telephonyApi\.listCommands/)
  assert.match(page, /current\.status === 'succeeded'/)
  assert.match(page, /current\.status === 'failed' \|\| current\.status === 'cancelled'/)
  assert.match(page, /Provider 状态确认超时/)
  assert.match(page, /return waitForCommand\(response\.action\)/)
  assert.match(telephonyApi, /export interface VoiceCommandRequest/)
  assert.match(telephonyApi, /warm_transfer_complete/)
  assert.match(telephonyApi, /warm_transfer_cancel/)
  assert.match(telephonyApi, /apiRequest<VoiceCommandResponse>/)
  assert.match(telephonyTypes, /export interface VoiceCommandRead/)
  assert.match(telephonyTypes, /export interface VoiceCommandResponse/)
  assert.match(telephonyTypes, /provider_status: string/)
  assert.match(telephonyTypes, /provider_reason\?: string \| null/)
})


test('operator mute remains local while hold and resume use durable Provider commands', () => {
  assert.match(page, /setLocalMicrophoneState/)
  assert.match(page, /setMicrophoneEnabled\(!\(nextMuted \|\| nextHeld\)\)/)
  assert.doesNotMatch(page, /recordAction\(next \? 'mute' : 'unmute'\)/)
  assert.match(page, /const action = next \? 'hold' : 'resume'/)
  assert.match(page, /recordAction\(action\)/)
})
