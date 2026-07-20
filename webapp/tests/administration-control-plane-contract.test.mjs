import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const TEST_DIR = dirname(fileURLToPath(import.meta.url))
const WEBAPP_ROOT = resolve(TEST_DIR, '..')

function read(relativePath) {
  const path = join(WEBAPP_ROOT, relativePath)
  assert.equal(existsSync(path), true, `missing required frontend path: ${relativePath}`)
  return readFileSync(path, 'utf8')
}

test('account and administration extend the canonical route registry and shell', () => {
  const router = read('src/router.tsx')
  const navigation = read('src/app/navigation.ts')
  const shell = read('src/app/AppShell.tsx')
  const accountRoute = read('src/routes/account.tsx')
  const administrationRoute = read('src/routes/administration.tsx')

  assert.match(router, /AccountRoute/)
  assert.match(router, /AdministrationRoute/)
  assert.match(navigation, /system-management|administration/)
  assert.match(navigation, /\/administration/)
  assert.match(navigation, /user\.manage/)
  assert.match(navigation, /security\.read/)
  assert.match(navigation, /audit\.read/)
  assert.match(shell, /to="\/account"/)
  assert.equal((shell.match(/<AppNavigation/g) ?? []).length, 1)
  assert.match(accountRoute, /lazy\(\(\) => import\('@\/features\/account\/lazy'\)\)/)
  assert.match(administrationRoute, /lazy\(\(\) => import\('@\/features\/administration\/lazy'\)\)/)
  assert.doesNotMatch(accountRoute, /AppShell/)
  assert.doesNotMatch(administrationRoute, /AppShell/)
})

test('identity UI consumes only canonical supportApi contracts and server role policy', () => {
  const api = read('src/lib/supportApi.ts')
  const account = read('src/features/account/AccountPage.tsx')
  const administration = read('src/features/administration/AdministrationPage.tsx')
  const users = read('src/features/administration/UserGovernance.tsx')
  const teams = read('src/features/administration/TeamGovernance.tsx')
  const audit = read('src/features/administration/SecurityAuditPanel.tsx')

  for (const path of [
    '/api/auth/change-password',
    '/api/admin/users',
    '/api/admin/identity/roles',
    '/api/admin/identity/teams',
    '/api/admin/security-audit',
  ]) assert.match(api, new RegExp(path.replaceAll('/', '\\/')))

  assert.doesNotMatch(api, /\bfetch\s*\(/)
  assert.match(account, /supportApi\.changePassword/)
  assert.match(administration, /supportApi\.rolePolicies/)
  assert.match(users, /default_capabilities/)
  assert.match(users, /supportApi\.createAdminUser/)
  assert.match(users, /supportApi\.updateAdminUser/)
  assert.match(teams, /supportApi\.createIdentityTeam/)
  assert.match(audit, /supportApi\.securityAudit/)
  assert.doesNotMatch(administration + users, /ROLE_CAPABILITIES|roleCapabilities|hardcodedCapabilities/)
})

test('administration remains inside MUI and canonical presentation authorities', () => {
  const files = [
    'src/features/account/AccountPage.tsx',
    'src/features/administration/AdministrationPage.tsx',
    'src/features/administration/UserGovernance.tsx',
    'src/features/administration/TeamGovernance.tsx',
    'src/features/administration/SecurityAuditPanel.tsx',
  ]
  for (const file of files) {
    const source = read(file)
    assert.match(source, /@mui\/material|OperatorPresentation/)
    assert.doesNotMatch(source, /className=/)
    assert.equal(existsSync(join(WEBAPP_ROOT, file.replace(/\.tsx$/, '.css'))), false)
  }
})
