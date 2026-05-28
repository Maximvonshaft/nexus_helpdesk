import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')

const apiClient = read('src/lib/api.ts')
const types = read('src/lib/types.ts')
const route = read('src/routes/outbound-email.tsx')
const appShell = read('src/layouts/AppShell.tsx')
const commandPalette = read('src/components/ui/CommandPalette.tsx')
const rbac = read('src/lib/rbac.ts')
const apiErrorMap = read('src/lib/apiErrorMap.ts')
const uxCopy = read('src/lib/uxCopy.ts')
const replyPanel = read('src/components/operator/CustomerReplyPanel.tsx')
const playwrightSmoke = read('e2e/smoke.spec.ts')
const realAdminSmoke = read('e2e/outbound-email-admin-real.spec.ts')
const playwrightConfig = read('playwright.config.ts')

test('outbound email API client matches backend PR-2 endpoints', () => {
  assert.match(apiClient, /outboundEmailAccounts: \(\) => request<OutboundEmailAccount\[\]>\('\/api\/admin\/outbound-email\/accounts'\)/)
  assert.match(apiClient, /outboundEmailAccount: \(accountId: number\) => request<OutboundEmailAccount>\(`\/api\/admin\/outbound-email\/accounts\/\$\{accountId\}`\)/)
  assert.match(apiClient, /createOutboundEmailAccount: \(payload: OutboundEmailAccountCreate\)/)
  assert.match(apiClient, /updateOutboundEmailAccount: \(accountId: number, payload: OutboundEmailAccountUpdate\)/)
  assert.match(apiClient, /enableOutboundEmailAccount: \(accountId: number\).*\/enable`, \{ method: 'POST' \}/)
  assert.match(apiClient, /disableOutboundEmailAccount: \(accountId: number\).*\/disable`, \{ method: 'POST' \}/)
  assert.match(apiClient, /testOutboundEmailAccount: \(accountId: number, payload: OutboundEmailTestSendRequest\)/)
  assert.match(apiClient, /\/test-send`, \{\s*method: 'POST',\s*body: JSON\.stringify\(payload\),/s)
})

test('outbound email frontend types expose masked account reads and subject sends', () => {
  assert.match(types, /export interface OutboundEmailAccount \{/)
  for (const field of [
    'host: string',
    'port: number',
    'username: string',
    'from_address: string',
    'reply_to?: string | null',
    'security_mode: OutboundEmailSecurityMode | string',
    'market_id?: number | null',
    'is_active: boolean',
    'priority: number',
    'health_status: string',
    'last_test_status?: string | null',
    'last_test_error?: string | null',
    'last_test_at?: string | null',
    'password_configured: boolean',
    'password_mask?: string | null',
  ]) {
    assert.match(types, new RegExp(field.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  }
  assert.match(types, /export type OutboundEmailTestSendRequest = \{[\s\S]*to_address: string[\s\S]*subject\?: string \| null[\s\S]*body\?: string \| null[\s\S]*\}/)
  assert.match(types, /export type OutboundSendPayload = \{[\s\S]*channel: string[\s\S]*subject\?: string \| null[\s\S]*body: string[\s\S]*\}/)
  assert.doesNotMatch(types, /password_encrypted/)
})

test('outbound email admin page protects secrets and supports test-send workflow', () => {
  assert.match(route, /path: '\/outbound-email'/)
  assert.match(route, /canManageChannels\(session\.data\)/)
  assert.match(route, /api\.outboundEmailAccounts/)
  assert.match(route, /api\.createOutboundEmailAccount/)
  assert.match(route, /api\.updateOutboundEmailAccount/)
  assert.match(route, /api\.enableOutboundEmailAccount/)
  assert.match(route, /api\.disableOutboundEmailAccount/)
  assert.match(route, /api\.testOutboundEmailAccount/)
  assert.match(route, /password_configured/)
  assert.match(route, /password_mask/)
  assert.match(route, /轮换密码/)
  assert.match(route, /留空表示不修改已保存密码/)
  assert.match(route, /Plain SMTP 不加密传输凭证/)
  assert.match(route, /测试发送会发出真实邮件/)
  assert.match(route, /<ConfirmDialog/)
  assert.doesNotMatch(route, /password_encrypted/)
})

test('outbound email navigation and command shortcuts are capability gated', () => {
  assert.match(rbac, /'\/outbound-email': \{ allOf: \[CAPABILITIES\.channelAccountManage\] \}/)
  assert.match(appShell, /to: '\/outbound-email'[\s\S]*label: 'SMTP 账号'[\s\S]*access: routeAccess\['\/outbound-email'\]/)
  assert.match(appShell, /渠道与授权'[\s\S]*'\/outbound-email'/)
  assert.match(commandPalette, /to: '\/outbound-email'[\s\S]*access: routeAccess\['\/outbound-email'\]/)
  assert.match(commandPalette, /新建 SMTP 账号/)
  assert.match(commandPalette, /维护 SMTP 账号/)
})

test('SMTP error taxonomy is mapped to operator-readable text', () => {
  for (const code of [
    'email_subject_required',
    'smtp_configuration_missing',
    'smtp_auth_failed',
    'smtp_tls_failed',
    'smtp_connect_timeout',
    'smtp_connect_failed',
    'smtp_sender_rejected',
    'smtp_recipient_rejected',
    'smtp_rate_limited',
    'smtp_message_rejected',
    'smtp_unexpected_error',
  ]) {
    assert.match(apiErrorMap, new RegExp(code))
  }
  assert.match(apiErrorMap, /error_code/)
  assert.match(uxCopy, /export const smtpFailureLabels/)
})

test('operator email send UX submits subject only for email', () => {
  assert.match(replyPanel, /function defaultEmailSubject/)
  assert.match(replyPanel, /function emailRecipient/)
  assert.match(replyPanel, /const selectedIsEmail = channel === 'email'/)
  assert.match(replyPanel, /<Field label="Email 主题" required/)
  assert.match(replyPanel, /subject: subject\.trim\(\)/)
  assert.match(replyPanel, /我确认这是 SMTP 外部邮件发送/)
  assert.match(replyPanel, /市场 SMTP 账号/)
  assert.match(replyPanel, /全局 fallback/)
})

test('outbound email browser smoke covers mock and real admin paths safely', () => {
  assert.match(playwrightSmoke, /admin can open outbound email configuration page/)
  assert.match(playwrightSmoke, /\/api\/admin\/outbound-email\/accounts/)
  assert.match(playwrightSmoke, /密码：\*\*\*\*\*\*\*\*/)
  assert.match(realAdminSmoke, /NEXUS_REAL_ADMIN_SMOKE/)
  assert.match(realAdminSmoke, /NEXUS_ADMIN_USERNAME/)
  assert.match(realAdminSmoke, /\/outbound-email/)
  assert.match(realAdminSmoke, /发送测试邮件/)
  assert.match(playwrightConfig, /PLAYWRIGHT_BASE_URL/)
})
