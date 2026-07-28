import { createWriteStream, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawn } from 'node:child_process'

const evidenceDir = process.env.NEXUS_FRONTEND_EVIDENCE_DIR
  || (process.env.GITHUB_ACTIONS === 'true' ? '/tmp/nexus-development' : join(tmpdir(), 'nexus-development'))
mkdirSync(evidenceDir, { recursive: true })
const logPath = join(evidenceDir, 'frontend-verify.log')
const log = createWriteStream(logPath, { flags: 'w' })
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm'

const stages = [
  ['architecture', npm, ['run', 'architecture']],
  ['lint', npm, ['run', 'lint']],
  ['typecheck', npm, ['run', 'typecheck']],
  ['unit-tests', npm, ['test']],
  ['build', npm, ['run', 'build']],
  ['route-splitting', process.execPath, ['scripts/assert-route-splitting.mjs']],
  ['size-report', npm, ['run', 'size-report']],
  ['draft-focused-browser', process.execPath, ['scripts/run-draft-focused-browser.mjs']],
]

function write(chunk) {
  log.write(chunk)
}

async function runStage(name, command, args) {
  const heading = `\n===== frontend verification: ${name} =====\n`
  process.stdout.write(heading)
  write(heading)
  const child = spawn(command, args, {
    cwd: process.cwd(),
    env: process.env,
    stdio: ['inherit', 'pipe', 'pipe'],
  })
  child.stdout.on('data', (chunk) => {
    process.stdout.write(chunk)
    write(chunk)
  })
  child.stderr.on('data', (chunk) => {
    process.stderr.write(chunk)
    write(chunk)
  })
  const result = await new Promise((resolve, reject) => {
    child.once('error', reject)
    child.once('close', (code, signal) => resolve({ code, signal }))
  })
  if (result.code !== 0) {
    const failure = `\nfrontend verification failed: ${name}; code=${result.code}; signal=${result.signal || 'none'}\n`
    process.stderr.write(failure)
    write(failure)
    throw new Error(`frontend_verification_failed:${name}`)
  }
}

try {
  for (const [name, command, args] of stages) {
    await runStage(name, command, args)
  }
  const success = '\nfrontend verification completed successfully\n'
  process.stdout.write(success)
  write(success)
} finally {
  await new Promise((resolve) => log.end(resolve))
}
