import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const runtimePage = readFileSync(new URL('../src/features/runtime/RuntimePage.tsx', import.meta.url), 'utf8')
const supportApi = readFileSync(new URL('../src/lib/supportApi.ts', import.meta.url), 'utf8')
const agentControl = readFileSync(new URL('../src/features/agent-control/AgentControlPage.tsx', import.meta.url), 'utf8')


test('runtime product exposes all deployment readiness profiles', () => {
  assert.match(runtimePage, /上线与激活/)
  assert.match(runtimePage, /releaseReadinessProfiles/)
  assert.match(runtimePage, /supportApi\.releaseReadiness\('controlled'\)/)
  assert.match(runtimePage, /supportApi\.releaseReadiness\('provider_canary'\)/)
  assert.match(runtimePage, /supportApi\.releaseReadiness\('full'\)/)
  assert.match(supportApi, /\/api\/admin\/release-readiness\?profile=/)
})


test('Agent configuration starts in test rather than production', () => {
  assert.match(
    agentControl,
    /useState<'test' \| 'staging' \| 'production'>\('test'\)/,
  )
})
