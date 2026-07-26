import { defineConfig } from '@playwright/test'

const port = Number(process.env.PLAYWRIGHT_PORT || 4173)
const externalBaseURL = process.env.PLAYWRIGHT_BASE_URL?.replace(/\/+$/, '')
const baseURL = externalBaseURL || `http://127.0.0.1:${port}`
const rcBrowser = (process.env.RC_RUN_BROWSER_SMOKE || '').toLowerCase() === 'true'

export default defineConfig({
  testDir: './e2e',
  snapshotPathTemplate: '{testDir}/__screenshots__/{arg}{ext}',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  // The RC journey is stateful and exact-head bound. A retry can create a
  // second synthetic conversation and mask the first failing browser stage.
  retries: rcBrowser ? 0 : (process.env.CI ? 1 : 0),
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  expect: {
    toHaveScreenshot: {
      animations: 'disabled',
      caret: 'hide',
      scale: 'css',
      maxDiffPixelRatio: 0.001,
    },
  },
  use: {
    baseURL,
    headless: true,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    launchOptions: rcBrowser
      ? {
          // RC browser traffic is loopback-only. The Playwright-pinned Chromium
          // revision bypasses ambient runner proxies.
          args: ['--no-proxy-server'],
        }
      : undefined,
  },
  webServer: externalBaseURL ? undefined : {
    command: `npm run preview -- --host 127.0.0.1 --port ${port}`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
