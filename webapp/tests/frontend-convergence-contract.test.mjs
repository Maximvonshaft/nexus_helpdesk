import test from 'node:test'
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'

test('frontend converges on one customer-service UI authority', () => {
  execFileSync(process.execPath, [resolve('scripts/assert-frontend-convergence.mjs')], {
    cwd: process.cwd(),
    stdio: 'pipe',
  })
})
