import { expect, test, type Page } from '@playwright/test'
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

async function textContrastViolations(page: Page): Promise<AccessibilityViolation[]> {
  return page.evaluate(() => {
    type Rgba = { r: number; g: number; b: number; a: number }
    const failures: AccessibilityViolation[] = []
    const parseColor = (value: string): Rgba | null => {
      const match = value.match(/rgba?\(\s*([\d.]+%?)[, ]+\s*([\d.]+%?)[, ]+\s*([\d.]+%?)(?:\s*[,/]\s*([\d.]+%?))?\s*\)/i)
      if (!match) return null
      const channelValue = (source: string) => Math.min(255, Math.max(0, source.endsWith('%') ? Number(source.slice(0, -1)) * 2.55 : Number(source)))
      const alphaValue = (source: string | undefined) => {
        if (source === undefined) return 1
        const raw = source.endsWith('%') ? Number(source.slice(0, -1)) / 100 : Number(source)
        return Math.min(1, Math.max(0, raw))
      }
      return {
        r: channelValue(match[1]),
        g: channelValue(match[2]),
        b: channelValue(match[3]),
        a: alphaValue(match[4]),
      }
    }
    const composite = (top: Rgba, bottom: Rgba): Rgba => {
      const alpha = top.a + bottom.a * (1 - top.a)
      if (alpha <= 0) return { r: 255, g: 255, b: 255, a: 1 }
      return {
        r: (top.r * top.a + bottom.r * bottom.a * (1 - top.a)) / alpha,
        g: (top.g * top.a + bottom.g * bottom.a * (1 - top.a)) / alpha,
        b: (top.b * top.a + bottom.b * bottom.a * (1 - top.a)) / alpha,
        a: alpha,
      }
    }
    const channel = (value: number) => {
      const normalized = value / 255
      return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4
    }
    const luminance = (color: Rgba) => 0.2126 * channel(color.r) + 0.7152 * channel(color.g) + 0.0722 * channel(color.b)
    const contrast = (left: Rgba, right: Rgba) => {
      const bright = Math.max(luminance(left), luminance(right))
      const dark = Math.min(luminance(left), luminance(right))
      return (bright + 0.05) / (dark + 0.05)
    }
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
      const classes = [...element.classList].slice(0, 2).join('.')
      return `${element.tagName.toLowerCase()}${classes ? `.${classes}` : ''}`
    }
    const backgroundFor = (element: Element) => {
      const layers: Rgba[] = []
      let current: Element | null = element
      while (current) {
        const color = parseColor(window.getComputedStyle(current).backgroundColor)
        if (color && color.a > 0) {
          layers.push(color)
          if (color.a >= 0.999) break
        }
        current = current.parentElement
      }
      let background: Rgba = { r: 255, g: 255, b: 255, a: 1 }
      for (const layer of layers.reverse()) background = composite(layer, background)
      return background
    }

    for (const element of document.querySelectorAll('body *')) {
      if (!visible(element)) continue
      if (element.closest(':disabled, [aria-disabled="true"], [aria-hidden="true"]')) continue
      const directText = [...element.childNodes]
        .filter((node) => node.nodeType === Node.TEXT_NODE)
        .map((node) => node.textContent || '')
        .join(' ')
        .replace(/\s+/g, ' ')
        .trim()
      if (!directText) continue

      const style = window.getComputedStyle(element)
      const foreground = parseColor(style.color)
      if (!foreground) continue
      const background = backgroundFor(element)
      const renderedForeground = composite(foreground, background)
      const ratio = contrast(renderedForeground, background)
      const fontSize = Number.parseFloat(style.fontSize)
      const numericWeight = Number.parseInt(style.fontWeight, 10)
      const fontWeight = Number.isFinite(numericWeight) ? numericWeight : style.fontWeight === 'bold' ? 700 : 400
      const largeText = fontSize >= 24 || (fontSize >= 18.66 && fontWeight >= 700)
      const required = largeText ? 3 : 4.5
      if (ratio + 0.01 < required) {
        failures.push({
          code: 'text-contrast',
          selector: selector(element),
          detail: `“${directText.slice(0, 48)}” 对比度 ${ratio.toFixed(2)}，要求 ${required.toFixed(1)}`,
        })
      }
    }
    return failures
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
    const controls = document.querySelectorAll([
      'button',
      'a[href]',
      'input[role="switch"]',
      '[role="tab"]',
      '[role="combobox"]',
    ].join(','))
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

test('200 percent text enlargement switches to structural compact layout without overlap or clipping', async ({ page }) => {
  const longIdentity = 'Extremely Long Multi-Country Operations Administrator Name 德语 Français Italiano'
  await page.setViewportSize({ width: 1366, height: 768 })
  await mockResponsiveConsole(page)
  await page.route('**/api/auth/me', (route) => json(route, {
    ...responsiveUser,
    display_name: longIdentity,
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
  const identity = page.getByTestId('operator-drawer-user-label')
  await expect(identity).toHaveText(longIdentity)
  expect(await identity.evaluate((element) => (
    element.scrollWidth <= element.clientWidth
    && element.scrollHeight <= element.clientHeight
  ))).toBe(true)
  expect(await formTextOverlaps(page)).toEqual([])
  expect(await undersizedPrimaryControls(page)).toEqual([])
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

test('canonical routes satisfy semantic, target-size and text-contrast contracts', async ({ page }) => {
  test.setTimeout(120_000)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await mockResponsiveConsole(page)

  for (const route of canonicalRoutes) {
    await page.goto(route.path)
    await expect(route.ready(page)).toBeVisible()
    expect(await semanticAccessibilityViolations(page), route.path).toEqual([])
    expect(await undersizedPrimaryControls(page), route.path).toEqual([])
    expect(await textContrastViolations(page), route.path).toEqual([])
  }
})

test('forced colors and reduced motion preserve required controls and visible focus', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' })
  await mockResponsiveConsole(page)
  await page.goto('/workspace')

  const skipLink = page.getByRole('link', { name: '跳到主要内容' })
  const menu = page.getByRole('button', { name: '打开主导航' })
  await expect(menu).toBeVisible()
  await page.keyboard.press('Tab')
  await expect(skipLink).toBeFocused()

  let focusEvidence: { outlineStyle: string; outlineWidth: string; transitionDuration: string } | null = null
  for (let step = 0; step < 12; step += 1) {
    await page.keyboard.press('Tab')
    focusEvidence = await page.evaluate(() => {
      const active = document.activeElement
      if (!(active instanceof HTMLElement) || active.getAttribute('aria-label') !== '打开主导航') return null
      const style = window.getComputedStyle(active)
      return {
        outlineStyle: style.outlineStyle,
        outlineWidth: style.outlineWidth,
        transitionDuration: style.transitionDuration,
      }
    })
    if (focusEvidence) break
  }

  expect(focusEvidence, 'the main navigation trigger must be keyboard reachable').not.toBeNull()
  expect(focusEvidence?.outlineStyle).not.toBe('none')
  expect(Number.parseFloat(focusEvidence?.outlineWidth || '0')).toBeGreaterThan(0)
  expect((focusEvidence?.transitionDuration || '')
    .split(',')
    .every((duration) => Number.parseFloat(duration) <= 0.01)).toBe(true)
  await expect(page.getByRole('tab', { name: '待处理' })).toBeVisible()
})
