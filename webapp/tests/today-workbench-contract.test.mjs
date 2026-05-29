import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const overviewRoute = readFileSync(resolve(root, 'src/routes/index.tsx'), 'utf8')
const apiClient = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')
const types = readFileSync(resolve(root, 'src/lib/types.ts'), 'utf8')
const appShell = readFileSync(resolve(root, 'src/layouts/AppShell.tsx'), 'utf8')
const commandPalette = readFileSync(resolve(root, 'src/components/ui/CommandPalette.tsx'), 'utf8')
const backendApi = readFileSync(resolve(root, '../backend/app/api/today_workbench.py'), 'utf8')
const backendMain = readFileSync(resolve(root, '../backend/app/main.py'), 'utf8')
const backendSchemas = readFileSync(resolve(root, '../backend/app/schemas.py'), 'utf8')

test('today workbench route is driven by the unified API client and template block contract', () => {
  assert.match(overviewRoute, /api\.todayWorkbench/)
  assert.doesNotMatch(overviewRoute, /fetch\(/)
  assert.match(overviewRoute, /data-testid="today-workbench-template-block"/)
  assert.match(overviewRoute, /今日工作台 \/ 我的优先事项/)
  assert.match(overviewRoute, /role-task-card/)
  assert.match(overviewRoute, /角色任务闭环/)
  assert.match(overviewRoute, /看到待办/)
  assert.match(overviewRoute, /进入队列/)
  assert.match(overviewRoute, /执行动作/)
  assert.match(overviewRoute, /得到反馈/)
  assert.match(overviewRoute, /审计回写/)
  assert.match(overviewRoute, /该角色可见入口/)
  assert.match(overviewRoute, /Command Center/)
  assert.match(overviewRoute, /交互状态/)
})

test('today workbench API client and TypeScript types expose the backend contract', () => {
  assert.match(apiClient, /TodayWorkbench/)
  assert.match(apiClient, /todayWorkbench: \(\) => request<TodayWorkbench>\('\/api\/today\/workbench'\)/)
  assert.match(types, /export interface TodayWorkbench/)
  assert.match(types, /visible_entrypoints: TodayWorkbenchEntrypoint\[\]/)
  assert.match(types, /interaction_states: TodayWorkbenchInteractionState\[\]/)
  assert.match(types, /command_center: TodayWorkbenchCommand\[\]/)
})

test('navigation and command palette use today workbench product language', () => {
  assert.match(appShell, /label: '今日工作台'/)
  assert.match(appShell, /先看今日工作台/)
  assert.doesNotMatch(appShell, /今日总览/)
  assert.match(commandPalette, /打开今日工作台/)
  assert.match(commandPalette, /角色任务/)
})

test('backend today workbench is a real authenticated view model, not frontend-only demo data', () => {
  assert.match(backendMain, /today_workbench/)
  assert.match(backendMain, /include_router\(today_workbench_router\)/)
  assert.match(backendSchemas, /class TodayWorkbenchRead/)
  assert.match(backendApi, /APIRouter\(prefix="\/api\/today"/)
  assert.match(backendApi, /@router\.get\("\/workbench", response_model=TodayWorkbenchRead\)/)
  assert.match(backendApi, /Depends\(get_current_user\)/)
  assert.match(backendApi, /ensure_capability\(current_user, CAP_TICKET_READ, db\)/)
  for (const model of ['Ticket', 'WebchatHandoffRequest', 'BackgroundJob', 'TicketOutboundMessage', 'AdminAuditLog', 'OutboundEmailAccount', 'IntegrationRequestLog']) {
    assert.match(backendApi, new RegExp(`\\b${model}\\b`))
  }
  for (const state of ['loading', 'empty', 'error', 'permission denied', 'unsaved changes']) {
    assert.match(backendApi, new RegExp(state))
  }
})
