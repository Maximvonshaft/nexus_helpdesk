import { readdirSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { resolve } from 'node:path'

const testsDir = resolve(process.cwd(), 'tests')
const testFiles = readdirSync(testsDir)
  .filter((name) => name.endsWith('.test.mjs'))
  .sort()

if (!testFiles.length) {
  console.error('No frontend contract tests were found.')
  process.exit(1)
}

for (const file of testFiles) {
  console.log(`\n=== FRONTEND CONTRACT: ${file} ===`)
  const result = spawnSync(process.execPath, ['--test', resolve(testsDir, file)], {
    cwd: process.cwd(),
    env: process.env,
    stdio: 'inherit',
  })
  if (result.error) {
    console.error(result.error)
    process.exit(1)
  }
  if (result.status !== 0) {
    console.error(`Frontend contract failed: ${file}`)
    process.exit(result.status || 1)
  }
}

console.log(`\nAll ${testFiles.length} frontend contract files passed.`)
