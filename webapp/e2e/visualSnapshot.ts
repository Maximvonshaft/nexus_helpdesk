import { createHash } from 'node:crypto'
import { expect, type Page, type TestInfo } from '@playwright/test'

export const VISUAL_SNAPSHOT_SHA256 = {
  'workspace-text-200-reflow-1366': 'b5e15ef87cdaa73c4e8f8995c9f9c1e3938d781bef783fb7b7728dd210eaac97',
  'workspace-loading-375': 'f61ff035450f1c6f2bcc5325f4cd6f7076e4681d082ce5e75a60ccc486ec01aa',
  'administration-normal-1440': 'a93b9ddefcc86c51066b393443600e6c0bc9d69d852ac941773ffd6af9c895f8',
  'workspace-empty-1440': '59aab11d38cd9ff991cd8e68912da6527f6f86859fa15ee33853957bbea80c88',
  'workspace-degraded-last-safe-1440': 'f0d1f095449bdf8fbc55c1b5ef072de2b04dd8d630a778495586ab2a3c5aa290',
  'workspace-stale-conflict-1440': 'e9337f5e999a8f8392f9ad9c7571e21c8bf8fb34fcc3f6bfc98698484de184e4',
  'workspace-repair-required-1440': '1bb01cf218faf7bf0cb7e70b0c4020f8a7e09181912c91f89ef62f580a8a9773',
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
