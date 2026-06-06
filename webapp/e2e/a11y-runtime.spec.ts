import { expect, test } from '@playwright/test'

test('runtime repair fixes dynamic WebChat and WebCall ARIA semantics', async ({ page }) => {
  await page.goto('/login')

  await page.evaluate(() => {
    const host = document.createElement('section')
    host.setAttribute('data-testid', 'a11y-runtime-fixture')

    const webchatList = document.createElement('div')
    webchatList.setAttribute('role', 'listbox')
    webchatList.setAttribute('aria-label', 'WebChat conversations')

    const selectedConversation = document.createElement('button')
    selectedConversation.type = 'button'
    selectedConversation.setAttribute('role', 'option')
    selectedConversation.setAttribute('aria-selected', 'true')
    selectedConversation.textContent = 'Ticket T-1001 Jane Cooper AI 暂停'
    webchatList.append(selectedConversation)

    const webcallFilters = document.createElement('div')
    webcallFilters.setAttribute('role', 'tablist')
    webcallFilters.setAttribute('aria-label', 'WebCall Operational Queue tabs')

    const incoming = document.createElement('button')
    incoming.type = 'button'
    incoming.className = 'button primary'
    incoming.textContent = 'Incoming'
    webcallFilters.append(incoming)

    const missed = document.createElement('button')
    missed.type = 'button'
    missed.className = 'button secondary'
    missed.textContent = 'Missed'
    webcallFilters.append(missed)

    host.append(webchatList, webcallFilters)
    document.body.append(host)
  })

  const conversations = page.locator('[aria-label="WebChat conversations"]')
  await expect(conversations).toHaveAttribute('role', 'list')

  const conversationButton = page.getByRole('button', { name: /打开 WebChat 会话：Ticket T-1001/ })
  await expect(conversationButton).toBeVisible()
  await expect(conversationButton).toHaveAttribute('aria-pressed', 'true')
  await expect(conversationButton).not.toHaveAttribute('role', 'option')
  await expect(conversationButton).not.toHaveAttribute('aria-selected', 'true')

  const webcallFilterGroup = page.getByRole('group', { name: 'WebCall Operational Queue filters' })
  await expect(webcallFilterGroup).toBeVisible()
  await expect(webcallFilterGroup).not.toHaveAttribute('role', 'tablist')
  await expect(webcallFilterGroup.getByRole('button', { name: 'Incoming' })).toHaveAttribute('aria-pressed', 'true')
  await expect(webcallFilterGroup.getByRole('button', { name: 'Missed' })).toHaveAttribute('aria-pressed', 'false')
})

test('runtime repair confirms dangerous WebChat mobile drawer actions before execution', async ({ page }) => {
  await page.goto('/login')

  await page.evaluate(() => {
    ;(window as unknown as { dangerousDrawerClicks: number }).dangerousDrawerClicks = 0
    const drawer = document.createElement('aside')
    drawer.className = 'v5-context-drawer'
    drawer.setAttribute('data-testid', 'a11y-danger-drawer-fixture')

    const release = document.createElement('button')
    release.type = 'button'
    release.textContent = '释放回队列'
    release.addEventListener('click', () => {
      ;(window as unknown as { dangerousDrawerClicks: number }).dangerousDrawerClicks += 1
    })

    drawer.append(release)
    document.body.append(drawer)
  })

  await page.getByRole('button', { name: '释放回队列' }).click()
  await expect(page.getByRole('dialog', { name: '确认释放回队列？' })).toBeVisible()
  await expect.poll(() => page.evaluate(() => (window as unknown as { dangerousDrawerClicks: number }).dangerousDrawerClicks)).toBe(0)

  await page.getByRole('button', { name: '取消' }).click()
  await expect(page.getByRole('dialog', { name: '确认释放回队列？' })).toHaveCount(0)
  await expect.poll(() => page.evaluate(() => (window as unknown as { dangerousDrawerClicks: number }).dangerousDrawerClicks)).toBe(0)

  await page.getByRole('button', { name: '释放回队列' }).click()
  const dialog = page.getByRole('dialog', { name: '确认释放回队列？' })
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: '释放回队列' }).click()
  await expect(page.getByRole('dialog', { name: '确认释放回队列？' })).toHaveCount(0)
  await expect.poll(() => page.evaluate(() => (window as unknown as { dangerousDrawerClicks: number }).dangerousDrawerClicks)).toBe(1)
})
