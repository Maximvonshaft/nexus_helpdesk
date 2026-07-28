import { readFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'

const TARGET_SPECS = [
  'e2e/operator-authorized-scope.spec.ts',
  'e2e/operator-workspace.spec.ts',
  'e2e/operator-ui-production-quality.spec.ts',
  'e2e/smoke.spec.ts',
]

function isDraftPullRequestCi() {
  if (process.env.CI !== 'true' || process.env.GITHUB_EVENT_NAME !== 'pull_request') {
    return false
  }
  const eventPath = process.env.GITHUB_EVENT_PATH
  if (!eventPath) return false
  try {
    const payload = JSON.parse(readFileSync(eventPath, 'utf8'))
    return payload?.pull_request?.draft === true
  } catch (error) {
    console.error('draft-focused-browser-event-read-failed', error)
    process.exit(2)
  }
}

function run(command, args, extraEnv = {}) {
  const result = spawnSync(command, args, {
    cwd: process.cwd(),
    env: { ...process.env, ...extraEnv },
    stdio: 'inherit',
    shell: process.platform === 'win32',
  })
  if (result.error) throw result.error
  if (result.status !== 0) process.exit(result.status ?? 1)
}

if (!isDraftPullRequestCi()) {
  console.log('Draft-focused browser gate skipped outside Draft pull-request CI.')
  process.exit(0)
}

console.log(`Running Draft-focused browser gate for ${TARGET_SPECS.length} exact specs.`)
run('npx', ['playwright', 'install', '--with-deps', 'chromium'])
run(
  'npx',
  ['playwright', 'test', ...TARGET_SPECS],
  { RC_RUN_BROWSER_SMOKE: 'true' },
)
