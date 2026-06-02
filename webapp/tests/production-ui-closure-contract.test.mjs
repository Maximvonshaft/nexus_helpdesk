import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(import.meta.dirname, '..')
const read = (path) => readFileSync(resolve(root, path), 'utf8')
const appShell = read('src/layouts/AppShell.tsx')
const router = read('src/router.tsx')
const customerSearch = read('src/routes/customer-search.tsx')
const commandPalette = read('src/components/ui/CommandPalette.tsx')
const dataTable = read('src/components/ui/DataTable.tsx')
const confirmDialog = read('src/components/ui/ConfirmDialog.tsx')
const button = read('src/components/ui/Button.tsx')

test('production IA uses workflow-first navigation', () => {
  assert.ok(appShell.includes("label: '工作台'"))
  assert.ok(appShell.includes("label: '工单与查询'"))
  assert.ok(appShell.includes("label: '运营与质量'"))
  assert.ok(appShell.includes("label: '配置管理'"))
  assert.ok(appShell.includes("label: '系统管理'"))
  assert.ok(appShell.includes("label: '客户 / 运单查询'"))
  assert.ok(!appShell.includes("label: '日常处理'"))
  assert.ok(!appShell.includes("label: '渠道与授权'"))
  assert.ok(!appShell.includes("label: '治理与运维'"))
})

test('AppShell centralizes direct URL permission fallback and nav semantics', () => {
  assert.ok(appShell.includes('routeRequirementForPath'))
  assert.ok(appShell.includes('routeDenied'))
  assert.ok(appShell.includes('NoAccessCard'))
  assert.ok(appShell.includes('aria-label="客服运营后台主导航"'))
  assert.ok(appShell.includes("aria-current={active ? 'page' : undefined}"))
  assert.ok(appShell.includes('role="main"'))
  assert.ok(appShell.includes('role="status" aria-live="polite"'))
})

test('customer and waybill search is a first-class route and command', () => {
  assert.ok(router.includes('CustomerSearchRoute'))
  assert.ok(router.includes('@/routes/customer-search'))
  assert.ok(customerSearch.includes("path: '/customer-search'"))
  assert.ok(customerSearch.includes('客户、运单与 CallerID 快查'))
  assert.ok(customerSearch.includes('api.casesPage({ q: normalized, limit: 25 })'))
  assert.ok(commandPalette.includes("id: 'customer-search'"))
  assert.ok(commandPalette.includes("to: '/customer-search'"))
})

test('shared UI primitives carry accessibility improvements', () => {
  assert.ok(dataTable.includes('<div className="table-wrap">'))
  assert.ok(dataTable.includes('scope="col"'))
  assert.ok(dataTable.includes('role="status" aria-live="polite"'))
  assert.ok(confirmDialog.includes('confirmRef'))
  assert.ok(confirmDialog.includes("event.key === 'Tab'"))
  assert.ok(confirmDialog.includes('aria-modal="true"'))
  assert.ok(button.includes('forwardRef<HTMLButtonElement'))
})
