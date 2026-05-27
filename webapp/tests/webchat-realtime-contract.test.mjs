import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')

const realtime = read('src/lib/webchatRealtime.ts')
const webchatRoute = read('src/routes/webchat.tsx')

test('webchat realtime adapter authenticates in hello and keeps polling fallback', () => {
  assert.match(realtime, /new WebSocket\(websocketUrl\(\)\)/)
  assert.match(realtime, /type: 'connection\.hello'/)
  assert.match(realtime, /access_token: token/)
  assert.doesNotMatch(realtime, /access_token=/)
  assert.match(realtime, /VITE_WEBCHAT_WS_ENABLED/)
  assert.match(realtime, /setStatus\('fallback'\)/)
  assert.match(realtime, /subscribe\.handoff_queue/)
  assert.match(realtime, /subscribe\.conversation/)
})

test('webchat route uses websocket events first and retains after_id polling fallback', () => {
  assert.match(webchatRoute, /useWebchatRealtime/)
  assert.match(webchatRoute, /realtime\.connected \? false : backoffMs/)
  assert.match(webchatRoute, /queue\.updated/)
  assert.match(webchatRoute, /client\.setQueryData<WebchatHandoffQueue>/)
  assert.match(webchatRoute, /webchatEvents/)
  assert.match(webchatRoute, /polling fallback/)
})
