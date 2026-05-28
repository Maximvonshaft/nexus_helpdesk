import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(import.meta.dirname, '..')
const emailRoute = readFileSync(resolve(root, 'src/routes/email.tsx'), 'utf8')
const router = readFileSync(resolve(root, 'src/router.tsx'), 'utf8')
const apiClient = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')
const rbac = readFileSync(resolve(root, 'src/lib/rbac.ts'), 'utf8')
const appShell = readFileSync(resolve(root, 'src/layouts/AppShell.tsx'), 'utf8')
const commandPalette = readFileSync(resolve(root, 'src/components/ui/CommandPalette.tsx'), 'utf8')

test('email workbench route uses unified routeAccess RBAC semantics', () => {
  assert.match(router, /EmailRoute/)
  assert.match(router, /@\/routes\/email/)
  assert.match(emailRoute, /path: '\/email'/)
  assert.match(rbac, /'\/email': \{ allOf: \[CAPABILITIES\.ticketRead\], anyOf: \[CAPABILITIES\.outboundDraftSave, CAPABILITIES\.outboundSend\] \}/)
  assert.match(emailRoute, /<RequireCapability requirement=\{routeAccess\['\/email'\]\}>/)
  assert.match(emailRoute, /const emailDraftAccess = \{ allOf: \[CAPABILITIES\.outboundDraftSave\] \}/)
  assert.match(emailRoute, /const emailSendAccess = \{ allOf: \[CAPABILITIES\.outboundSend\] \}/)
  assert.doesNotMatch(emailRoute, /emailWorkbenchAccess/)
})

test('email workbench is reachable from AppShell navigation and command palette', () => {
  assert.match(appShell, /to: '\/email'[\s\S]*label: 'Email 工作台'[\s\S]*access: routeAccess\['\/email'\]/)
  assert.match(appShell, /\{ label: '工作台', items: \['\/', '\/webchat', '\/webcall', '\/email'\] \}/)
  assert.match(appShell, /\{ label: '业务处理', items: \['\/workspace', '\/bulletins'\] \}/)
  assert.match(commandPalette, /id: 'email-workbench'[\s\S]*label: '打开 Email 工作台'[\s\S]*to: '\/email'[\s\S]*access: routeAccess\['\/email'\]/)
})

test('email queue filter uses tokenized channel markers instead of loose substring regex', () => {
  assert.match(emailRoute, /const EMAIL_QUEUE_TOKENS = new Set\(\['email', 'mail', 'smtp', 'imap', 'pop3'\]\)/)
  assert.match(emailRoute, /replace\(\/\\be\[-_\\s\]\?mail\\b\/g, 'email'\)/)
  assert.match(emailRoute, /split\(\/\[\^a-z0-9\]\+\/\)\.some\(\(token\) => EMAIL_QUEUE_TOKENS\.has\(token\)\)/)
  assert.doesNotMatch(emailRoute, /\/email\|mail\|smtp\/i/)
})

test('email workbench closes draft save, outbound send, and timeline refresh loops', () => {
  assert.match(apiClient, /saveOutboundDraft: \(ticketId: number, payload: OutboundSendPayload\)/)
  assert.match(apiClient, /`\/api\/tickets\/\$\{ticketId\}\/outbound\/draft`/)
  assert.match(emailRoute, /api\.saveOutboundDraft\(activeCase\.id, \{ channel: 'email', subject: subject\.trim\(\), body: body\.trim\(\) \}\)/)
  assert.match(emailRoute, /api\.sendOutboundMessage\(activeCase\.id, \{ channel: 'email', subject: subject\.trim\(\), body: body\.trim\(\) \}\)/)
  assert.match(emailRoute, /api\.ticketOutboundChannelCapabilities\(activeCase\.id\)/)
  assert.match(emailRoute, /api\.ticketTimeline\(selectedId as number, \{ limit: 30 \}\)/)
  assert.match(emailRoute, /invalidateQueries\(\{ queryKey: \['ticketTimeline', activeCase\.id\] \}\)/)
  assert.match(emailRoute, /保存草稿和发送都会进入 ticket timeline\/ticket event audit/)
  assert.doesNotMatch(emailRoute, /\bfetch\s*\(/)
})
