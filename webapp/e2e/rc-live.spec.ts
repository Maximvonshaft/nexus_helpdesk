import { writeFileSync } from 'node:fs'
import { expect, test, type Page, type Response } from '@playwright/test'

const adminUsername = process.env.RC_TEST_ADMIN_USERNAME || ''
const adminPassword = process.env.RC_TEST_ADMIN_PASSWORD || ''
const sourceSha = process.env.RC_SOURCE_SHA || ''
const baseURL = (process.env.PLAYWRIGHT_BASE_URL || '').replace(/\/+$/, '')
const browserStageFile = process.env.RC_BROWSER_STAGE_FILE || ''
const operatorTokenKey = 'helpdesk-webapp-token'
const rcRequired = (process.env.RC_RUN_BROWSER_SMOKE || '').toLowerCase() === 'true'
const rcConfigured = Boolean(
  adminUsername
  && adminPassword
  && /^[0-9a-f]{40}$/.test(sourceSha)
  && /^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/.test(baseURL),
)

function rcUrl(path: string): string {
  return new URL(path, `${baseURL}/`).toString()
}

function markStage(stage: string): void {
  if (!/^[a-z0-9_-]{1,56}$/.test(stage)) throw new Error('invalid RC browser stage')
  console.log(`RC_BROWSER_STAGE=${stage}`)
  if (browserStageFile) {
    writeFileSync(browserStageFile, `${stage}\n`, { encoding: 'utf8', mode: 0o600 })
  }
}

function classifyBrowserError(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error)
  const networkCode = raw.match(/net::(ERR_[A-Z0-9_]+)/)?.[1]
  if (networkCode) return networkCode.toLowerCase().replaceAll('_', '-').slice(0, 48)
  const normalized = raw.toLowerCase()
  if (normalized.includes('timeout')) return 'navigation-timeout'
  if (
    normalized.includes('target page')
    || normalized.includes('browser has been closed')
    || normalized.includes('context has been closed')
  ) return 'target-closed'
  if (normalized.includes('navigation') && normalized.includes('interrupted')) return 'navigation-interrupted'
  return 'unknown-navigation-error'
}

function reportBoundedBrowserError(error: unknown): void {
  const raw = error instanceof Error ? error.message : String(error)
  const bounded = raw
    .replaceAll(baseURL, '{base_url}')
    .replaceAll(adminUsername, '{admin_username}')
    .replaceAll(adminPassword, '{admin_password}')
    .replaceAll(sourceSha, '{source_sha}')
    .slice(0, 400)
  const detailHex = Buffer.from(bounded, 'utf8').toString('hex')
  console.log(`RC_BROWSER_DETAIL_HEX=${detailHex}`)
  markStage(`error-${classifyBrowserError(error)}`)
}

async function navigate(page: Page, path: string): Promise<Response | null> {
  try {
    return await page.goto(rcUrl(path), {
      waitUntil: 'commit',
      timeout: 20_000,
    })
  } catch (error) {
    reportBoundedBrowserError(error)
    throw error
  }
}

function waitForPublicMessagePost(page: Page): Promise<Response> {
  return page.waitForResponse((candidate) => {
    const url = new URL(candidate.url())
    return candidate.request().method() === 'POST'
      && /\/api\/webchat\/conversations\/wc_[^/]+\/messages$/.test(url.pathname)
  }, { timeout: 25_000 })
}

async function activateWorkspaceSurface(page: Page, name: '客户沟通' | '操作'): Promise<void> {
  const tab = page.getByRole('tab', { name, exact: true })
  if (await tab.count()) await tab.first().click()
}

test.describe.configure({ mode: 'serial' })

test.describe('controlled candidate live WebChat', () => {
  test.skip(!rcRequired, 'RC live browser journey runs only in Controlled Candidate')

  test.beforeAll(() => {
    expect(
      rcConfigured,
      'RC_RUN_BROWSER_SMOKE=true requires admin credentials, an exact source SHA, and a loopback PLAYWRIGHT_BASE_URL',
    ).toBe(true)
  })

  test('RC public WebChat supports consecutive messages, human ownership, reply and closure', async ({ page, context }) => {
  test.setTimeout(90_000)
  const firstMessage = `RC browser synthetic message 1 ${sourceSha.slice(0, 12)}`
  const secondMessage = `RC browser synthetic message 2 ${sourceSha.slice(0, 12)}`
  const operatorReply = `RC operator synthetic reply ${sourceSha.slice(0, 12)}`
  const closeNote = `RC synthetic human resolution ${sourceSha.slice(0, 12)}`
  const message = firstMessage
  const publicPage = await context.newPage()

  markStage('public-navigation')
  const initResponsePromise = publicPage.waitForResponse((candidate) => {
    const url = new URL(candidate.url())
    return candidate.request().method() === 'POST' && url.pathname === '/api/webchat/init'
  }, { timeout: 25_000 })
  const navigationResponse = await navigate(publicPage, '/webchat/demo/')
  markStage('public-committed')
  expect(navigationResponse).not.toBeNull()
  expect(navigationResponse?.ok()).toBeTruthy()

  markStage('public-page')
  await expect(publicPage.locator('script[data-auto-open="true"]')).toHaveCount(1, { timeout: 20_000 })

  markStage('public-widget')
  await expect.poll(
    () => publicPage.evaluate(() => typeof (window as typeof window & { NexusDeskWebChat?: unknown }).NexusDeskWebChat === 'object'),
    { timeout: 20_000 },
  ).toBe(true)
  await expect(publicPage.locator('.nd-webchat-panel[data-open="true"]')).toBeVisible({ timeout: 20_000 })
  const input = publicPage.locator('.nd-webchat-input')
  const send = publicPage.locator('.nd-webchat-send')
  await expect(input).toBeEnabled({ timeout: 20_000 })
  const initResponse = await initResponsePromise
  expect(initResponse.ok()).toBeTruthy()
  markStage('public-init')

  markStage('public-send-first')
  await input.fill(firstMessage)
  const firstMessageRequest = waitForPublicMessagePost(publicPage)
  await send.click()
  const messageResponse = await firstMessageRequest
  expect(messageResponse.ok()).toBeTruthy()

  const conversationMatch = new URL(messageResponse.url()).pathname.match(
    /^\/api\/webchat\/conversations\/(wc_[A-Za-z0-9_-]+)\/messages$/,
  )
  expect(conversationMatch).not.toBeNull()
  const conversationId = conversationMatch?.[1] || ''
  expect(conversationId).toMatch(/^wc_[A-Za-z0-9_-]+$/)
  const operatorSessionKey = `webchat:${conversationId}`

  markStage('public-first-persisted')
  await expect(publicPage.locator('.nd-webchat-msg.visitor', { hasText: firstMessage })).toBeVisible()
  await expect(send).toBeEnabled({ timeout: 15_000 })

  markStage('public-send-second')
  await input.fill(secondMessage)
  const secondMessageRequest = waitForPublicMessagePost(publicPage)
  await send.click()
  const secondMessageResponse = await secondMessageRequest
  expect(secondMessageResponse.ok()).toBeTruthy()
  expect(new URL(secondMessageResponse.url()).pathname).toContain(`/${conversationId}/messages`)

  markStage('public-second-persisted')
  await expect(publicPage.locator('.nd-webchat-msg.visitor', { hasText: secondMessage })).toBeVisible()
  await expect(send).toBeEnabled({ timeout: 15_000 })

  markStage('login-navigation')
  const loginResponse = await navigate(page, '/login')
  markStage('login-form')
  expect(loginResponse).not.toBeNull()
  expect(loginResponse?.ok()).toBeTruthy()
  await page.getByLabel('账号').fill(adminUsername)
  await page.locator('#login-password').fill(adminPassword)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).not.toHaveURL(/\/login$/)

  markStage('operator-navigation')
  const operatorPath = `/webchat?session=${encodeURIComponent(operatorSessionKey)}`
  const operatorResponse = await navigate(page, operatorPath)
  markStage('operator-workspace')
  expect(operatorResponse).not.toBeNull()
  expect(operatorResponse?.ok()).toBeTruthy()
  await expect(page).toHaveURL(
    rcUrl(`/workspace?session=${encodeURIComponent(operatorSessionKey)}`),
    { timeout: 25_000 },
  )
  await expect(page.getByTestId('operator-workspace')).toBeVisible({ timeout: 20_000 })

  markStage('operator-case')
  await expect(
    page.getByTestId('operator-message-timeline').getByText(message, { exact: true }),
  ).toBeVisible({ timeout: 25_000 })
  await expect(
    page.getByTestId('operator-message-timeline').getByText(secondMessage, { exact: true }),
  ).toBeVisible({ timeout: 25_000 })

  markStage('operator-handoff-requested')
  await activateWorkspaceSurface(page, '操作')
  await expect(page.getByRole('heading', { name: '人工接管', exact: true })).toBeVisible({ timeout: 25_000 })
  await expect(page.getByRole('button', { name: '接受会话', exact: true })).toBeVisible({ timeout: 25_000 })

  markStage('operator-online')
  const presence = page.getByRole('combobox', { name: '客服状态' })
  await expect(presence).toBeVisible({ timeout: 20_000 })
  await presence.click()
  const stateUpdatePromise = page.waitForResponse((candidate) => {
    const url = new URL(candidate.url())
    return candidate.request().method() === 'PUT' && url.pathname === '/api/operator/agent-state'
  }, { timeout: 25_000 })
  await page.getByRole('option', { name: '在线', exact: true }).click()
  const stateUpdateResponse = await stateUpdatePromise
  expect(stateUpdateResponse.ok()).toBeTruthy()
  const assignedState = await stateUpdateResponse.json() as { active_conversations?: number; max_concurrent_conversations?: number }
  expect(Number(assignedState.active_conversations || 0)).toBeGreaterThanOrEqual(1)

  markStage('operator-handoff-assigned')
  await activateWorkspaceSurface(page, '客户沟通')
  const replyField = page.getByLabel('回复客户')
  await expect(replyField).toBeEnabled({ timeout: 25_000 })

  markStage('operator-reply')
  await replyField.fill(operatorReply)
  const replyResponsePromise = page.waitForResponse((candidate) => {
    const url = new URL(candidate.url())
    return candidate.request().method() === 'POST'
      && url.pathname === `/api/operator/conversations/${conversationId}/reply`
  }, { timeout: 25_000 })
  await page.getByRole('button', { name: '发送回复', exact: true }).click()
  const replyResponse = await replyResponsePromise
  expect(replyResponse.ok()).toBeTruthy()
  await expect(
    page.getByTestId('operator-message-timeline').getByText(operatorReply, { exact: true }),
  ).toBeVisible({ timeout: 25_000 })

  markStage('customer-received')
  await publicPage.bringToFront()
  await expect(publicPage.locator('.nd-webchat-msg', { hasText: operatorReply })).toBeVisible({ timeout: 25_000 })

  markStage('operator-close')
  await page.bringToFront()
  await activateWorkspaceSurface(page, '操作')
  await expect(page.getByRole('heading', { name: '结束当前会话', exact: true })).toBeVisible({ timeout: 25_000 })
  await page.getByLabel('会话结果').click()
  await page.getByRole('option', { name: '人工在线解决', exact: true }).click()
  await page.getByLabel('处理说明').fill(closeNote)
  await page.getByRole('button', { name: '核对结束信息', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '确认结束当前会话？' })).toBeVisible()
  const closeResponsePromise = page.waitForResponse((candidate) => {
    const url = new URL(candidate.url())
    return candidate.request().method() === 'POST'
      && url.pathname === `/api/operator/conversations/${conversationId}/close`
  }, { timeout: 25_000 })
  await page.getByRole('button', { name: '确认结束并释放名额', exact: true }).click()
  const closeResponse = await closeResponsePromise
  expect(closeResponse.ok()).toBeTruthy()
  const closeResult = await closeResponse.json() as { conversation_id?: string; status?: string; outcome?: string }
  expect(closeResult).toMatchObject({
    conversation_id: conversationId,
    status: 'closed',
    outcome: 'human_resolved',
  })

  markStage('capacity-released')
  const operatorToken = await page.evaluate((key) => sessionStorage.getItem(key), operatorTokenKey)
  expect(operatorToken).toBeTruthy()
  const supportResponse = await page.request.get(
    rcUrl('/api/support/conversations?view=all&channel=webchat&limit=100'),
    { headers: { Authorization: `Bearer ${operatorToken}` } },
  )
  expect(supportResponse.ok()).toBeTruthy()
  const supportPayload = await supportResponse.json() as { items?: Array<Record<string, unknown>> }
  const closedConversation = supportPayload.items?.find((item) => item.session_key === operatorSessionKey)
  expect(closedConversation).toMatchObject({
    session_key: operatorSessionKey,
    status: 'closed',
    handoff_status: 'closed',
    active_agent_id: null,
  })
  const stateResponse = await page.request.get(
    rcUrl('/api/operator/agent-state'),
    { headers: { Authorization: `Bearer ${operatorToken}` } },
  )
  expect(stateResponse.ok()).toBeTruthy()
  const finalState = await stateResponse.json() as { active_conversations?: number; available_capacity?: number; max_concurrent_conversations?: number }
  expect(Number(finalState.active_conversations || 0)).toBeLessThanOrEqual(Number(assignedState.active_conversations || 0))
  expect(Number(finalState.available_capacity || 0) + Number(finalState.active_conversations || 0)).toBe(Number(finalState.max_concurrent_conversations || 0))

  markStage('completed')
  })
})
