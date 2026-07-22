import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const read = (path) => readFileSync(join(ROOT, path), 'utf8')


test('MFA uses one auth transport and does not issue a token before challenge completion', () => {
  const api = read('src/lib/supportApi.ts')
  const hooks = read('src/hooks/useAuth.ts')
  const login = read('src/routes/login.tsx')
  const account = read('src/features/account/MfaAccountPanel.tsx')
  const credentials = read('src/features/administration/CredentialGovernance.tsx')

  for (const endpoint of [
    '/api/auth/mfa/login/verify',
    '/api/auth/mfa/status',
    '/api/auth/mfa/setup/begin',
    '/api/auth/mfa/setup/confirm',
    '/api/auth/mfa/setup/cancel',
    '/api/auth/mfa/recovery-codes/regenerate',
    '/api/auth/mfa/disable',
    '/api/admin/identity/users/${userId}/reset-mfa',
  ]) assert.match(api, new RegExp(endpoint.replaceAll('/', '\\/').replaceAll('$', '\\$').replaceAll('{', '\\{').replaceAll('}', '\\}')))

  assert.doesNotMatch(api + hooks + login + account + credentials, /\bfetch\s*\(/)
  assert.match(hooks, /if \(!isMfaLoginChallenge\(result\)\) setSupportToken\(result\.access_token\)/)
  assert.match(hooks, /verifyMfaLogin/)
  assert.match(login, /验证码或恢复码/)
  assert.match(account, /恢复码只能使用一次/)
  assert.match(account, /setRecoveryCodes\(\[\]\)/)
  assert.match(credentials, /resetAdminUserMfa/)
  assert.doesNotMatch(account, /localStorage|sessionStorage|console\./)
})


test('runtime recovery extends the canonical runtime surface and existing backend commands', () => {
  const api = read('src/lib/supportApi.ts')
  const runtime = read('src/features/runtime/RuntimePage.tsx')
  const recovery = read('src/features/runtime/RuntimeRecoveryPanel.tsx')
  const router = read('src/router.tsx')

  assert.match(api, /\/api\/admin\/queues\/summary/)
  assert.match(api, /\/api\/admin\/jobs\/requeue-dead/)
  assert.match(api, /\/api\/admin\/outbound\/requeue-dead/)
  assert.match(runtime, /RuntimeRecoveryPanel/)
  assert.match(recovery, /runtime\.manage/)
  assert.match(recovery, /不代表业务结果已经完成/)
  assert.equal((router.match(/RuntimeRoute/g) ?? []).length, 2)
  assert.doesNotMatch(recovery, /\bfetch\s*\(/)
})


test('email account governance remains inside the single channels route and write-only secret contract', () => {
  const api = read('src/lib/supportApi.ts')
  const lazy = read('src/features/channels/lazy.tsx')
  const controlPlane = read('src/features/channels/ChannelsControlPlane.tsx')
  const email = read('src/features/channels/EmailAccountGovernance.tsx')
  const router = read('src/router.tsx')

  for (const endpoint of [
    '/api/admin/outbound-email/accounts',
    '/api/admin/outbound-email/accounts/${accountId}/enable',
    '/api/admin/outbound-email/accounts/${accountId}/disable',
    '/api/admin/outbound-email/accounts/${accountId}/test-send',
  ]) assert.match(api, new RegExp(endpoint.replaceAll('/', '\\/').replaceAll('$', '\\$').replaceAll('{', '\\{').replaceAll('}', '\\}')))

  assert.match(lazy, /ChannelsControlPlane/)
  assert.match(controlPlane, /<ChannelsPage/)
  assert.match(controlPlane, /<EmailAccountGovernance/)
  assert.match(email, /留空保留当前密码/)
  assert.match(email, /testOutboundEmailAccount/)
  assert.equal((router.match(/ChannelsRoute/g) ?? []).length, 2)
  assert.doesNotMatch(email, /password_encrypted|imap_password_encrypted|\bfetch\s*\(/)
  assert.equal(existsSync(join(ROOT, 'src/features/channels/EmailAccountGovernance.css')), false)
})
