import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const webappRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const read = (relative) => fs.readFileSync(path.join(webappRoot, relative), 'utf8')

const router = read('src/router.tsx')
const navigation = read('src/app/navigation.ts')
const shell = read('src/app/AppShell.tsx')
const authenticatedPage = read('src/app/AuthenticatedAppPage.tsx')
const identityApi = read('src/lib/identityApi.ts')
const account = read('src/features/account/AccountPage.tsx')
const administration = read('src/features/administration/AdministrationPage.tsx')


test('identity administration has one route, one API client and no parallel shell', () => {
  assert.match(router, /routes\/administration/)
  assert.match(router, /routes\/account/)
  assert.match(navigation, /canonicalRoute: '\/administration'/)
  assert.doesNotMatch(administration + account, /function\s+AppShell|function\s+AppNavigation/)
  assert.match(administration, /identityApi\.createUser/)
  assert.match(administration, /identityApi\.updateUser/)
  assert.match(administration, /identityApi\.resetPassword/)
  assert.match(administration, /identityApi\.logoutUserEverywhere/)
  assert.match(identityApi, /\/api\/admin\/users/)
  assert.doesNotMatch(identityApi, /\/api\/(?:identity|accounts|access-control)\/users/)
})


test('admin user loading is complete and never falls back to the bounded legacy list', () => {
  assert.match(identityApi, /type AdminUsersPage/)
  assert.match(identityApi, /while \(true\)/)
  assert.match(identityApi, /next_cursor/)
  assert.match(identityApi, /用户分页游标未推进/)
  assert.doesNotMatch(identityApi, /legacy=true/)
})


test('nullable identity fields use explicit canonical commands instead of silent PATCH nulls', () => {
  assert.match(identityApi, /method: 'DELETE'/)
  assert.match(identityApi, /\/api\/admin\/users\/\$\{userId\}\/email/)
  assert.match(identityApi, /\/api\/admin\/users\/\$\{userId\}\/team/)
  assert.match(identityApi, /method: 'PUT'/)
  assert.match(identityApi, /email !== null/)
})


test('account security is projected through the one session read and rotates the token', () => {
  assert.match(identityApi, /\/api\/auth\/change-password/)
  assert.match(identityApi, /setSupportToken\(response\.access_token\)/)
  assert.match(identityApi, /\/api\/auth\/logout-all/)
  assert.doesNotMatch(identityApi + account, /\/api\/auth\/security|accountSecurity/)
  assert.match(account, /session\.data\.last_login_at/)
  assert.match(account, /session\.data\.password_changed_at/)
  assert.match(account, /修改后所有旧会话立即失效/)
  assert.match(authenticatedPage, /must_change_password/)
  assert.match(authenticatedPage, /to: '\/account'/)
})


test('the application shell exposes account security without a second administration navigation', () => {
  assert.match(shell, /href="\/account"/)
  assert.match(shell, /href="\/administration"/)
  assert.match(shell, /canAccessAdministration/)
  assert.equal((navigation.match(/key: 'administration'/g) || []).length, 1)
  assert.equal((router.match(/AdministrationRoute/g) || []).length, 2)
})
