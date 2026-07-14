import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

const webappRoot = resolve(import.meta.dirname, '..')
const repoRoot = resolve(webappRoot, '..')
const workflowRoot = join(repoRoot, '.github', 'workflows')
const relevantWorkflows = [
  'external-channel-retirement-gate.yml',
  'frontend-ci.yml',
  'frontend-convergence-gate.yml',
  'webapp-build.yml',
]
const temporaryWorkflows = [
  'generate-radix-lockfile.yml',
  'refresh-frontend-lockfile.yml',
]

function read(path) {
  return readFileSync(path, 'utf8')
}

for (const name of temporaryWorkflows) {
  assert.equal(existsSync(join(workflowRoot, name)), false, `temporary frontend write workflow must be deleted: ${name}`)
}

const actionRefPattern = /^\s*uses:\s*([^\s@]+)@([^\s#]+)\s*$/gm
for (const name of relevantWorkflows) {
  const path = join(workflowRoot, name)
  assert.ok(existsSync(path), `required frontend workflow is missing: ${name}`)
  const source = read(path)
  assert.match(source, /permissions:\s*\n\s+contents:\s*read\b/, `${name} must use read-only contents permission`)
  assert.doesNotMatch(source, /contents:\s*write|pull-requests:\s*write|actions:\s*write|security-events:\s*write/, `${name} must not grant write permissions`)

  const actionRefs = [...source.matchAll(actionRefPattern)]
  assert.ok(actionRefs.length > 0, `${name} must contain explicit immutable actions`)
  for (const [, action, ref] of actionRefs) {
    if (action.startsWith('./')) continue
    assert.match(ref, /^[0-9a-f]{40}$/i, `${name} action is not pinned to a full commit SHA: ${action}@${ref}`)
  }

  for (const match of source.matchAll(/^\s*uses:\s*actions\/checkout@[0-9a-f]{40}\s*$/gmi)) {
    const following = source.slice(match.index, match.index + 400)
    assert.match(following, /persist-credentials:\s*false/, `${name} checkout must disable credential persistence`)
  }

  if (/npm ci\b/.test(source)) {
    assert.doesNotMatch(source, /npm ci(?!\s+--ignore-scripts)/, `${name} npm ci must disable lifecycle scripts`)
  }
}

console.log(JSON.stringify({
  ok: true,
  workflows: relevantWorkflows,
  temporaryWorkflowsAbsent: temporaryWorkflows,
}, null, 2))
