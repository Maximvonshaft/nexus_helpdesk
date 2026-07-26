import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

const root = path.resolve(import.meta.dirname, '..')
const shell = fs.readFileSync(path.join(root, 'src/app/AppShell.tsx'), 'utf8')
const control = fs.readFileSync(path.join(root, 'src/app/IncomingVoiceCallControl.tsx'), 'utf8')
const route = fs.readFileSync(path.join(root, 'src/routes/webcall.tsx'), 'utf8')
const lazyRoute = fs.readFileSync(path.join(root, 'src/features/webcall/lazy.tsx'), 'utf8')
const context = fs.readFileSync(path.join(root, 'src/features/webcall/WebCallOperatorContext.tsx'), 'utf8')
const api = fs.readFileSync(path.join(root, 'src/lib/telephonyApi.ts'), 'utf8')


test('canonical AppShell owns the single incoming voice offer surface', () => {
  assert.match(shell, /IncomingVoiceCallControl/)
  assert.match(control, /telephonyApi\.incomingOffers/)
  assert.match(control, /telephonyApi\.rejectOffer/)
  assert.match(control, /\/webcall\//)
  assert.match(control, /该来电仅分配给当前坐席/)
  assert.doesNotMatch(control, /acceptHandoff/)
})


test('voice acceptance is deferred to the one lazy canonical WebCall route', () => {
  assert.match(route, /lazy\(\(\) => import\('@\/features\/webcall\/lazy'\)\)/)
  assert.equal((route.match(/LazyWebCallRouteContent/g) ?? []).length, 2)
  assert.doesNotMatch(route, /WebCallPage/)
  assert.doesNotMatch(route, /WebCallOperatorContext/)
  assert.match(lazyRoute, /WebCallPage/)
  assert.match(lazyRoute, /WebCallOperatorContext/)
  assert.equal((lazyRoute.match(/<WebCallPage/g) ?? []).length, 1)
  assert.equal((lazyRoute.match(/<WebCallOperatorContext/g) ?? []).length, 1)
  assert.match(context, /INCOMING_VOICE_CONTEXT_PREFIX/)
  assert.match(api, /\/api\/webchat\/admin\/voice\/sessions\?status=ringing/)
  assert.match(api, /\/reject/)
})
