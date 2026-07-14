import test from 'node:test'
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'

const auditScripts = [
  'assert-frontend-convergence.mjs',
  'assert-frontend-reachability.mjs',
  'assert-frontend-css-usage.mjs',
  'assert-frontend-visible-copy.mjs',
  'assert-frontend-toolchain-usage.mjs',
  'assert-frontend-workflow-security.mjs',
]

test('frontend converges on one reachable, dependency-clean, supply-chain-governed authority', () => {
  for (const script of auditScripts) {
    execFileSync(process.execPath, [resolve('scripts', script)], {
      cwd: process.cwd(),
      stdio: 'pipe',
    })
  }
})
