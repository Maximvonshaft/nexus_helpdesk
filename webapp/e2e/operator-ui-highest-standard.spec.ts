import { expect, test, type Page, type TestInfo } from '@playwright/test'
import {
  canonicalRoutes,
  json,
  mockResponsiveConsole,
  responsiveUser,
} from './fixtures/responsiveConsole'

type AccessibilityViolation = {
  code: string
  selector: string
  detail: string
}

async function semanticAccessibilityViolations(page: Page): Promise<AccessibilityViolation[]> {
  return page.evaluate(() => {
    const violations: AccessibilityViolation[] = []
    const visible = (element: Element) => {
      const style = window.getComputedStyle(element)
      const rect = element.getBoundingClientRect()
      return style.display !== 'none'
        && style.visibility !== 'hidden'
        && Number.parseFloat(style.opacity || '1') > 0
        && rect.width > 0
        && rect.height > 0
    }
    const selector = (element: Element) => {
      const id = element.getAttribute('id')
      if (id) return `#${id}`
      const role = element.getAttribute('role')
      const name = element.getAttribute('aria-label') || element.textContent?.trim().slice(0, 40)
      return `${element.tagName.toLowerCase()}${role ? `[role=${role}]` : ''}${name ? `:${name}` : ''}`
    }
    const labelledByText = (element: Element) => (element.getAttribute('aria-labelledby') || '')
      .split(/\s+/)
      .filter(Boolean)
      .map((id) => document.getElementById(id)?.textContent?.trim() || '')
      .join(' ')
      .trim()
    const associatedLabelText = (element: Element) => {
      if (!(element instanceof HTMLInputElement || element instanceof HTMLSelectElement || element instanceof HTMLTextAreaElement)) return ''
      return [...(element.labels || [])].map((label) => label.textContent?.trim() || '').join(' ').trim()
    }

    const seenIds = new Set<string>()
    for (const element of document.querySelectorAll('[id]')) {
      const id = element.id
      if (seenIds.has(id)) violations.push({ code: 'duplicate-id', selector: `#${id}`, detail: 'ID 在同一页面重复出现' })
      seenIds.add(id)
    }

    const visibleMains = [...document.querySelectorAll('main')].filter(visible)
    if (visibleMains.length !== 1) {
      violations.push({ code: 'main-landmark', selector: 'main', detail: `需要且只能有一个可见 main，当前为 ${visibleMains.length}` })
    }

    const interactiveSelector = [
      'button',
      'a[href]',
      'input:not([type="hidden"])',
      'select',
      'textarea',
      '[role="button"]',
      '[role="link"]',
      '[role="tab"]',
      '[role="switch"]',
      '[role="combobox"]',
    ].join(',')
    const interactive = [...document.querySelectorAll(interactiveSelector)].filter(visible)
    for (const element of interactive) {
      if (element.closest('[aria-hidden="true"]')) {
        violations.push({ code: 'focusable-aria-hidden', selector: selector(element), detail: '可交互元素位于 aria-hidden 区域' })
      }
      if (element instanceof HTMLElement && element.tabIndex > 0) {
        violations.push({ code: 'positive-tabindex', selector: selector(element), detail: '禁止使用正数 tabindex 改写自然焦点顺序' })
      }
      const name = [
        element.getAttribute('aria-label') || '',
        labelledByText(element),
        associatedLabelText(element),
        element.getAttribute('title') || '',
        element.textContent?.trim() || '',
        element instanceof HTMLInputElement ? element.value.trim() : '',
      ].find((value) => value.trim()) || ''
      if (!name) violations.push({ code: 'accessible-name', selector: selector(element), detail: '可交互元素缺少可访问名称' })
    }

    for (const element of interactive) {
      const nested = element.querySelector(interactiveSelector)
      if (nested && visible(nested)) {
        violations.push({ code: 'nested-interactive', selector: selector(element), detail: `包含嵌套交互元素 ${selector(nested)}` })
      }
    }

    return violations
  })
}

async function formTextOverlaps(page: Page) {
  return page.evaluate(() => {
    const failures: string[] = []
    const visible = (element: Element) => {
      const style = window.getComputedStyle(element)
      const rect = element.getBoundingClientRect()
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0
    }
    const textRect = (element: Element) => {
      if (!element.textContent?.trim()) return null
      const range = document.createRange()
      range.selectNodeContents(element)
      const rect = range.getBoundingClientRect()
      return rect.width > 0 && rect.height > 0 ? rect : null
    }
    const intersects = (left: DOMRect, right: DOMRect) => {
      const width = Math.min(left.right, right.right) - Math.max(left.left, right.left)
      const height = Math.min(left.bottom, right.bottom) - Math.max(left.top, right.top)
      return width > 1 && height > 1
    }

    for (const control of document.querySelectorAll('.MuiFormControl-root')) {
      if (!visible(control)) continue
      const label = control.querySelector('.MuiInputLabel-root')
      const value = control.querySelector('.MuiSelect-select, input:not([type="hidden"]), textarea')
      if (!label || !value || !visible(label) || !visible(value)) continue
      const labelRect = textRect(label)
      const valueRect = textRect(value)
      if (labelRect && valueRect && intersects(labelRect, valueRect)) {
        failures.push(`${label.textContent?.trim() || '未命名字段'} 与当前值发生文字重叠`)
      }
    }
    return failures
  })
}

async function undersizedPrimaryControls(page: Page) {
  return page.evaluate(() => {
    const failures: string[] = []
    const visible = (element: Element) => {
      const style = window.getComputedStyle(element)
      const rect = element.getBoundingClientRect()
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0
    }
    const controls = document.querySelectorAll('button, .MuiButtonBase-root, [role="tab"], [role="switch"], [role="combobox"]')
    for (const control of controls) {
      if (!visible(control)) continue
      const rect = control.getBoundingClientRect()
      if (rect.height + 0.5 < 44) {
        failures.push(`${control.getAttribute('aria-label') || control.textContent?.trim().slice(0, 40) || control.tagName}: ${rect.height.toFixed(1)}px`)
      }
    }
    return failures
  })
}

async function capture(page: Page, testInfo: TestInfo, name: string) {
  await page.screenshot({ path: testInfo.outputPath(`${name}.png`), fullPage: true, animations: 'disabled' })
}

test('200 percent text enlargement switches to structural compact layout without overlap', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1366, height: 768 })
  await mockResponsiveConsole(page)
  await page.route('**/api/auth/me', (route) => json(route, {
    ...responsiveUser,
    display_name: 'Extremely Long Multi-Country Operations Administrator Name 德语 Français Italiano',
  }))
  await page.goto('/workspace')
  await page.addStyleTag({ content: 'html { font-size: 200% !important; }' })

  await expect(page.getByRole('button', { name: '打开主导航' })).toBeVisible()
  await expect(page.getByRole('tab', { name: '待处理' })).toBeVisible()
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  expect(await formTextOverlaps(page)).toEqual([])
  expect(await undersizedPrimaryControls(page)).toEqual([])

  await page.getByRole('button', { name: '打开主导航' }).click()
  await expect(page.locator('#nd-mobile-navigation')).toBeVisible()
  expect(await formTextOverlaps(page)).toEqual([])
  expect(await undersizedPrimaryControls(page)).toEqual([])
  await capture(page, testInfo, 'workspace-text-200-reflow-1366')
})

test('canonical routes reflow to the 320 CSS pixel release floor', async ({ page }) => {
  test.setTimeout(120_000)
  await page.setViewportSize({ width: 320, height: 720 })
  await mockResponsiveConsole(page)

  for (const route of canonicalRoutes) {
    await page.goto(route.path)
    await expect(route.ready(page)).toBeVisible()
    await expect(page.getByRole('main')).toBeVisible()
    await expect.poll(
      () => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      { message: `${route.path} overflowed at the 320px reflow floor` },
    ).toBe(true)
  }
})

test('canonical routes satisfy the automated semantic accessibility contract', async ({ page }) => {
  test.setTimeout(120_000)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await mockResponsiveConsole(page)

  for (const route of canonicalRoutes) {
    await page.goto(route.path)
    await expect(route.ready(page)).toBeVisible()
    expect(await semanticAccessibilityViolations(page), route.path).toEqual([])
    expect(await undersizedPrimaryControls(page), route.path).toEqual([])
  }
})

test('forced colors and reduced motion preserve required controls and visible focus', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' })
  await mockResponsiveConsole(page)
  await page.goto('/workspace')

  const menu = page.getByRole('button', { name: '打开主导航' })
  await expect(menu).toBeVisible()
  await menu.focus()
  await expect(menu).toBeFocused()
  const focus = await menu.evaluate((element) => {
    const style = window.getComputedStyle(element)
    return { outlineStyle: style.outlineStyle, outlineWidth: style.outlineWidth, transitionDuration: style.transitionDuration }
  })
  expect(focus.outlineStyle).not.toBe('none')
  expect(Number.parseFloat(focus.outlineWidth)).toBeGreaterThan(0)
  expect(focus.transitionDuration.split(',').every((duration) => Number.parseFloat(duration) <= 0.01)).toBe(true)
  await expect(page.getByRole('tab', { name: '待处理' })).toBeVisible()
})
