import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const backendRoot = resolve(root, '..', 'backend')
const route = readFileSync(resolve(root, 'src/routes/permissions-audit.tsx'), 'utf8')
const router = readFileSync(resolve(root, 'src/router.tsx'), 'utf8')
const appShell = readFileSync(resolve(root, 'src/layouts/AppShell.tsx'), 'utf8')
const commandPalette = readFileSync(resolve(root, 'src/components/ui/CommandPalette.tsx'), 'utf8')
const apiClient = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')
const types = readFileSync(resolve(root, 'src/lib/types.ts'), 'utf8')
const rbac = readFileSync(resolve(root, 'src/lib/rbac.ts'), 'utf8')
const adminApi = readFileSync(resolve(backendRoot, 'app/api/admin.py'), 'utf8')
const schemas = readFileSync(resolve(backendRoot, 'app/schemas.py'), 'utf8')
const permissions = readFileSync(resolve(backendRoot, 'app/services/permissions.py'), 'utf8')

test('permissions audit route is registered and surfaced through operator entrypoints', () => {
  assert.match(router, /PermissionsAuditRoute/)
  assert.match(router, /@\/routes\/permissions-audit/)
  assert.match(route, /path: '\/permissions-audit'/)
  assert.match(appShell, /to: '\/permissions-audit'[\s\S]*access: routeAccess\['\/permissions-audit'\]/)
  assert.match(commandPalette, /to: '\/permissions-audit'[\s\S]*access: routeAccess\['\/permissions-audit'\]/)
})

test('permissions audit uses centralized rbac and unified api client', () => {
  assert.match(rbac, /auditRead: 'audit\.read'/)
  assert.match(rbac, /'\/permissions-audit': \{ anyOf: \[CAPABILITIES\.auditRead, CAPABILITIES\.userManage\] \}/)
  assert.match(rbac, /CAPABILITIES\.auditRead[\s\S]*查看权限审计/)
  assert.match(types, /export interface PermissionsAuditDashboard/)
  assert.match(apiClient, /permissionsAudit: \(\) => request<PermissionsAuditDashboard>\('\/api\/admin\/permissions-audit'\)/)
  assert.match(route, /<RequireCapability requirement=\{routeAccess\['\/permissions-audit'\]\}>/)
  assert.match(route, /data-testid="permissions-audit-workbench"/)
  assert.match(route, /api\.permissionsAudit/)
  assert.doesNotMatch(route, /\bfetch\s*\(/)
})

test('permissions audit workbench remains readonly', () => {
  for (const mutation of ['createUser', 'updateUser', 'activateUser', 'deactivateUser', 'resetUserPassword']) {
    assert.doesNotMatch(route, new RegExp(`api\\.${mutation}\\b`))
  }
  assert.match(route, /Auditor readonly/)
})

test('backend exposes audit.read and returns parsed audit evidence', () => {
  assert.match(permissions, /CAP_AUDIT_READ = "audit\.read"/)
  assert.match(permissions, /UserRole\.auditor[\s\S]*CAP_AUDIT_READ/)
  assert.match(permissions, /def ensure_can_read_audit/)
  assert.match(adminApi, /@router\.get\('\/permissions-audit', response_model=PermissionsAuditRead\)/)
  assert.match(adminApi, /ensure_can_read_audit\(current_user, db\)/)
  assert.match(adminApi, /AdminAuditLog/)
  assert.match(adminApi, /old_value=_read_json_object\(row\.old_value_json\)/)
  assert.match(adminApi, /new_value=_read_json_object\(row\.new_value_json\)/)
  assert.match(schemas, /class PermissionsAuditRead/)
  assert.match(schemas, /audit_logs: list\[AdminAuditLogRead\]/)
  assert.match(schemas, /summary: PermissionsAuditSummaryRead/)
})
