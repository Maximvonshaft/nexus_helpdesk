import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const router = readFileSync(resolve(root, 'src/router.tsx'), 'utf8')
const route = readFileSync(resolve(root, 'src/routes/security.tsx'), 'utf8')
const api = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')
const types = readFileSync(resolve(root, 'src/lib/types.ts'), 'utf8')
const rbac = readFileSync(resolve(root, 'src/lib/rbac.ts'), 'utf8')
const appShell = readFileSync(resolve(root, 'src/layouts/AppShell.tsx'), 'utf8')
const commandPalette = readFileSync(resolve(root, 'src/components/ui/CommandPalette.tsx'), 'utf8')

test('security audit route is registered and capability gated', () => {
  assert.match(router, /SecurityRoute/)
  assert.match(router, /@\/routes\/security/)
  assert.match(route, /path: '\/security'/)
  assert.match(route, /RequireCapability requirement=\{routeAccess\['\/security'\]\}/)
  assert.match(rbac, /securityRead: 'security\.read'/)
  assert.match(rbac, /auditRead: 'audit\.read'/)
  assert.match(rbac, /'\/security': \{ anyOf: \[CAPABILITIES\.userManage, CAPABILITIES\.securityRead, CAPABILITIES\.auditRead\] \}/)
})

test('security audit uses centralized API client and typed backend contract', () => {
  assert.match(api, /SecurityAudit/)
  assert.match(api, /securityAudit: \(params\?: \{ limit\?: number \}\)/)
  assert.match(api, /\/api\/admin\/security-audit\?/)
  assert.match(types, /export interface SecurityAudit/)
  assert.match(types, /export interface SecurityCapabilityUser/)
  assert.match(types, /export interface AdminAuditLog/)
  assert.match(types, /export interface SecurityAuditSummary/)
})

test('security audit is exposed in shell navigation and command palette', () => {
  assert.match(appShell, /to: '\/security'[\s\S]*access: routeAccess\['\/security'\]/)
  assert.match(appShell, /'\/security'/)
  assert.match(commandPalette, /id: 'security-audit'/)
  assert.match(commandPalette, /to: '\/security'[\s\S]*access: routeAccess\['\/security'\]/)
})

test('security page renders the expected audit and matrix anchors', () => {
  assert.match(route, /data-testid="security-audit-lens"/)
  assert.match(route, /data-testid="security-capability-matrix"/)
  assert.match(route, /data-testid="security-recent-audit"/)
  assert.match(route, /api\.securityAudit\(\{ limit: 40 \}\)/)
})
