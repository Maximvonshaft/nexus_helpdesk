# WebCall P0 Gap Closure Runbook

## Scope

This patch closes the minimum production gaps for the first WebChat-originated WebCall canary:

```text
visitor WebChat -> voice session -> incoming queue -> agent accept/reject/end -> LiveKit room -> ticket timeline evidence -> text fallback -> feature-flag rollback
```

It intentionally does not enable recording, realtime transcription, AI voice, SIP, PSTN, outbound calling, or phone-number routing.

## Required validation

```bash
PYTHONPATH=backend pytest \
  backend/tests/test_webchat_voice_api.py \
  backend/tests/test_webchat_voice_p0_gap_closure.py \
  backend/tests/test_livekit_voice_provider.py \
  backend/tests/test_webchat_voice_room_compensation.py \
  backend/tests/test_webchat_voice_static_headers.py \
  backend/tests/test_webchat_voice_mock_ui_static.py \
  backend/tests/test_webchat_voice_p0_static.py \
  -q

npm --prefix webapp run typecheck
npm --prefix webapp run build
npm --prefix webapp test
```

## Deployment stance

Deploy code first with `WEBCHAT_VOICE_ENABLED=false`. Enable LiveKit only after staging canary and manual two-browser audio proof pass.

## Rollback

```env
WEBCHAT_VOICE_ENABLED=false
WEBCHAT_VOICE_PROVIDER=mock
WEBCHAT_VOICE_RECORDING_ENABLED=false
WEBCHAT_VOICE_TRANSCRIPTION_ENABLED=false
```

Do not drop `webchat_voice_*` tables during emergency rollback.
