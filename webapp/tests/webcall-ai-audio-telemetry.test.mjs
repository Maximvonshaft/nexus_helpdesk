import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'
import assert from 'node:assert/strict'

const root = resolve(import.meta.dirname, '..')
const route = readFileSync(resolve(root, 'src/routes/webcall-ai.tsx'), 'utf8')
const styles = readFileSync(resolve(root, 'src/styles.css'), 'utf8')

test('WebCall AI Start call records client audio publish telemetry without tokens', () => {
  assert.match(route, /client-audio-telemetry/)
  assert.match(route, /reportClientAudioTelemetry/)
  assert.match(route, /get_user_media_success/)
  assert.match(route, /get_user_media_failure/)
  assert.match(route, /local_track_state/)
  assert.match(route, /livekit_publish_success/)
  assert.match(route, /livekit_publish_failure/)
  assert.match(route, /selected_audio_input_label/)
  assert.match(route, /selected_audio_input_device_id_hash/)
  assert.match(route, /local_track_ready_state/)
  assert.match(route, /local_track_enabled/)
  assert.match(route, /local_track_muted/)
  assert.match(route, /console\.info\('\[webcall-ai-audio\]'/)
  assert.doesNotMatch(route, /console\.info\([^)]*participant_token/)
})

test('WebCall AI Start call displays a realtime microphone level bar', () => {
  assert.match(route, /data-testid="webcall-ai-mic-meter"/)
  assert.match(route, /data-testid="webcall-ai-mic-level-bar"/)
  assert.match(route, /createMediaStreamSource/)
  assert.match(route, /getByteTimeDomainData/)
  assert.match(styles, /\.mic-meter/)
  assert.match(styles, /\.mic-level-bar/)
})
