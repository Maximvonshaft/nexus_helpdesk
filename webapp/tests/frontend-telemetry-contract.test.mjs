import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const telemetry = fs.readFileSync(new URL('../src/lib/frontendTelemetry.ts', import.meta.url), 'utf8')
const apiClient = fs.readFileSync(new URL('../src/lib/apiClient.ts', import.meta.url), 'utf8')
const main = fs.readFileSync(new URL('../src/main.tsx', import.meta.url), 'utf8')
const webVitals = fs.readFileSync(new URL('../src/lib/webVitals.ts', import.meta.url), 'utf8')

test('frontend telemetry reuses the canonical transport and cannot recursively observe itself', () => {
  assert.match(telemetry, /import \{ apiRequest, getSupportToken \} from '@\/lib\/apiClient'/)
  assert.doesNotMatch(telemetry, /\bfetch\(/)
  assert.match(telemetry, /MAX_BATCH_SIZE = 50/)
  assert.match(telemetry, /new URL\(detail\.path, window\.location\.origin\)\.pathname/)
  assert.match(apiClient, /FRONTEND_METRICS_PATH/)
  assert.match(apiClient, /shouldEmitFrontendLatency/)
})

test('web vitals and API latency initialize once at the application root', () => {
  assert.match(main, /initFrontendTelemetry\(\)/)
  assert.match(main, /initWebVitals\(\)/)
  assert.match(webVitals, /VITE_WEB_VITALS_ENABLED \|\| 'true'/)
  assert.match(webVitals, /LCP/)
  assert.match(webVitals, /CLS/)
  assert.match(webVitals, /INP/)
})
