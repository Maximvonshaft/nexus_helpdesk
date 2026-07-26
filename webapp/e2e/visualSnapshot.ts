import { createHash } from 'node:crypto'
import { expect, type Page, type TestInfo } from '@playwright/test'

export const VISUAL_SNAPSHOT_SHA256 = {
  'workspace-text-200-reflow-1366': 'dfb30cd03fe2109e260b4f0312a7f336355ea1270759157e9c713de551ff2fb6',
  'workspace-loading-375': 'b11e7517b77c6d8ee3672472ce235d7e6e82a614544cf1ae72225997a7dec5f0',
  'administration-normal-1440': 'a93b9ddefcc86c51066b393443600e6c0bc9d69d852ac941773ffd6af9c895f8',
  'workspace-empty-1440': '11467064562854c2b971eb38b4e94345493be4b88abceea64e9984e8f123f4c0',
  'workspace-degraded-last-safe-1440': '5d4c91ed1972dd98fb6700f9078b1cd696298d690489188aaac5d8be71be89f1',
  'workspace-stale-conflict-1440': '44ec53d76804f6aefc7f6b95b35a934d455a15dd2a53ce8ecc79fa81b813d88d',
  'workspace-repair-required-1440': 'fa639a38dd57abe4a7b8e4832c0a5d9e5f3a4b8064e38d405bf9de8959c8d727',
} as const

export type VisualSnapshotName = keyof typeof VISUAL_SNAPSHOT_SHA256

export async function expectVisualSnapshot(
  page: Page,
  testInfo: TestInfo,
  name: VisualSnapshotName,
) {
  const actual = await page.screenshot({
    fullPage: true,
    animations: 'disabled',
    caret: 'hide',
    scale: 'css',
  })
  const actualSha256 = createHash('sha256').update(actual).digest('hex')
  const expectedSha256 = VISUAL_SNAPSHOT_SHA256[name]

  await testInfo.attach(`${name}.png`, { body: actual, contentType: 'image/png' })

  expect(
    actualSha256,
    `${name} visual signature changed; inspect the attached PNG before updating the reviewed SHA-256 authority`,
  ).toBe(expectedSha256)
}
