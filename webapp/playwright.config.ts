import { defineConfig } from '@playwright/test'

const port = Number(process.env.PLAYWRIGHT_PORT || 4173)
const externalBaseURL = process.env.PLAYWRIGHT_BASE_URL?.replace(/\/+$/, '')
const baseURL = externalBaseURL || `http://127.0.0.1:${port}`

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL,
    headless: true,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    launchOptions: {
      channel: 'chrome',
      // RC browser traffic is loopback-only. Do not inherit an ambient runner
      // proxy for 127.0.0.1; that can make curl pass while Chrome navigation
      // fails before the main document commits.
      args: ['--no-proxy-server'],
    },
  },
  webServer: externalBaseURL ? undefined : {
    command: `npm run preview -- --host 127.0.0.1 --port ${port}`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
