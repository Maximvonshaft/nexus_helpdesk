# Human WebCall Stale Session Reconciler

## Purpose

The reconciler closes expired non-terminal Human WebCall sessions that were left open after a missed callback, browser disconnect, worker interruption, or provider lifecycle gap.

It does not enable WebCall AI, recording, transcription, raw audio storage, outbound writes, or any customer-visible AI voice entry.

## Behavior

- Default mode is dry-run.
- Apply mode requires `--apply`.
- `--limit` is required to stay bounded and is capped at 1000.
- Eligible rows have `expires_at` older than the grace window, no `ended_at`, and a non-terminal status.
- Stale `accepted` or `active` sessions become `ended`.
- Stale `created` or `ringing` sessions become `missed`.
- Output contains safe session public IDs and status metadata only. It must not include phone numbers, emails, visitor tokens, fingerprints, message bodies, transcripts, cookies, bearer tokens, provider secrets, or API tokens.

## Dry Run

```bash
PYTHONPATH=backend python backend/scripts/reconcile_stale_webchat_voice_sessions.py --limit 100 --older-than-seconds 300 --json
```

## Apply

```bash
PYTHONPATH=backend python backend/scripts/reconcile_stale_webchat_voice_sessions.py --apply --limit 100 --older-than-seconds 300 --json
```

Run apply in small batches. If `skipped_count` is greater than zero, rerun another bounded batch after verifying the previous result.

## Health Check

The existing admin-only provider runtime status endpoint includes a `human_webcall` section:

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" /api/admin/provider-runtime/status
```

Expected disabled-by-default posture:

```json
{
  "webchat_voice_enabled": false,
  "provider": "livekit",
  "recording_enabled": false,
  "transcription_enabled": false,
  "active_session_count": 0,
  "ringing_session_count": 0,
  "stale_active_session_count": 0,
  "stale_ringing_session_count": 0,
  "readiness_verdict": "disabled",
  "warnings": []
}
```

Provider may be `mock` in local development when `WEBCHAT_VOICE_PROVIDER` is not set.
