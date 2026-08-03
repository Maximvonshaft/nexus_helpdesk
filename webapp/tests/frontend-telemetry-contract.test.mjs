import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const telemetry = fs.readFileSync(new URL('../src/lib/frontendTelemetry.ts', import.meta.url), 'utf8')
const apiClient = fs.readFileSync(new URL('../src/lib/apiClient.ts', import.meta.url), 'utf8')
const main = fs.readFileSync(new URL('../src/main.tsx', import.meta.url), 'utf8')
const application = fs.readFileSync(new URL('../src/application.tsx', import.meta.url), 'utf8')
const webVitals = fs.readFileSync(new URL('../src/lib/webVitals.ts', import.meta.url), 'utf8')

test('frontend telemetry reuses the canonical transport and cannot recursively observe itself', () => {
  assert.match(telemetry, /import \{ apiRequest, getSupportToken \} from '@\/lib\/apiClient'/)
  assert.doesNotMatch(telemetry, /\bfetch\(/)
  assert.match(telemetry, /MAX_BATCH_SIZE = 50/)
  assert.match(telemetry, /new URL\(detail\.path, window\.location\.origin\)\.pathname/)
  assert.match(apiClient, /FRONTEND_METRICS_PATH/)
  assert.match(apiClient, /shouldEmitFrontendLatency/)
})

test('web-vital units are converted exactly once by the backend authority', () => {
  assert.match(telemetry, /value: raw/)
  assert.doesNotMatch(telemetry, /raw\s*\/\s*1_000/)
  assert.match(telemetry, /backend owns the single[\s\S]*conversion to histogram seconds/)
})

test('HTTP responses and transport failures produce one latency outcome per attempt', () => {
  assert.match(apiClient, /const handledResponseError = error instanceof ApiError \|\| error instanceof AuthExpiredError/)
  assert.match(apiClient, /if \(!handledResponseError\) \{[\s\S]*emitFrontendLatency\(/)
  assert.match(apiClient, /if \(!retryable \|\| attempt > 0 \|\| handledResponseError\) break/)
})

test('catalog bootstrap imports the one application root before telemetry starts', () => {
  assert.match(main, /await import\('\.\/application'\)/)
  assert.doesNotMatch(main, /initFrontendTelemetry\(\)|initWebVitals\(\)/)
  assert.equal((application.match(/initFrontendTelemetry\(\)/g) ?? []).length, 1)
  assert.equal((application.match(/initWebVitals\(\)/g) ?? []).length, 1)
  assert.match(webVitals, /VITE_WEB_VITALS_ENABLED \|\| 'true'/)
  assert.match(webVitals, /LCP/)
  assert.match(webVitals, /CLS/)
  assert.match(webVitals, /INP/)
})
