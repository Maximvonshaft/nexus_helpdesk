import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const route = readFileSync(resolve(root, 'src/routes/security-audit.tsx'), 'utf8')
const api = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')
const rbac = readFileSync(resolve(root, 'src/lib/rbac.ts'), 'utf8')
const types = readFileSync(resolve(root, 'src/lib/types.ts'), 'utf8')
const router = readFileSync(resolve(root, 'src/router.tsx'), 'utf8')
const appShell = readFileSync(resolve(root, 'src/layouts/AppShell.tsx'), 'utf8')
const commandPalette = readFileSync(resolve(root, 'src/components/ui/CommandPalette.tsx'), 'utf8')

test('security audit uses the unified API client and typed backend contract', () => {
  assert.match(api, /SecurityAuditRead/)
  assert.match(api, /securityAudit:\s*\(params\?:/)
  assert.match(api, /request<SecurityAuditRead>\(`\/api\/admin\/security-audit/)
  assert.match(types, /export interface SecurityAuditRead/)
  assert.doesNotMatch(route, /\bfetch\(/)
})

test('security audit route is capability gated for admin and auditor read paths', () => {
  assert.match(rbac, /securityRead: 'security\.read'/)
  assert.match(rbac, /auditRead: 'audit\.read'/)
  assert.match(rbac, /'\/security-audit': \{ anyOf: \[CAPABILITIES\.securityRead, CAPABILITIES\.auditRead, CAPABILITIES\.userManage\] \}/)
  assert.match(route, /RequireCapability/)
  assert.match(route, /routeAccess\['\/security-audit'\]/)
  assert.match(router, /SecurityAuditRoute/)
  assert.match(route, /path: '\/security-audit'/)
})

test('security audit is exposed in AppShell and CommandPalette', () => {
  assert.match(appShell, /to: '\/security-audit'[\s\S]*权限与审计/)
  assert.match(appShell, /routeAccess\['\/security-audit'\]/)
  assert.match(commandPalette, /security-audit/)
  assert.match(commandPalette, /复核权限与审计/)
})

test('security audit workbench remains read-only in the frontend', () => {
  assert.match(route, /Capability Matrix/)
  assert.match(route, /User Lens/)
  assert.match(route, /Audit Trail/)
  assert.match(route, /secret_values_exposed/)
  assert.match(route, /auditor_readonly|审计员只读/)
  assert.doesNotMatch(route, /useMutation/)
  assert.doesNotMatch(route, /api\.(createUser|updateUser|activateUser|deactivateUser|resetUserPassword)/)
})
