# NexusDesk WebCall Canary Readiness Runbook

## Purpose

This runbook defines the pre-release canary gate for the WebCall operator console and voice lifecycle path after PR-WC-1 through PR-WC-5.

The canary is intentionally non-deploying. It validates readiness before any production rollout decision.

## Safety boundary

The canary must not:

- deploy production;
- run docker compose up;
- modify deploy/.env.prod;
- modify LiveKit provider credentials;
- modify database migrations;
- request microphone permission during page load;
- render provider credentials or voice participant credentials into operator UI;
- enable SIP/PSTN, recording, transcription, AI voice, or outbound calling.

## Required local command

```bash
cd /opt/nexus_helpdesk_worktrees/webcall_b3_canary
OUT_DIR=/tmp/nexus_webcall_canary_manual REQUIRE_CLEAN_WORKTREE=1 bash scripts/webcall_canary_readiness.sh
```

## Optional HTTP readiness command

Use this only against an explicitly selected non-production or already-running target.

```bash
cd /opt/nexus_helpdesk_worktrees/webcall_b3_canary
NEXUS_CANARY_BASE_URL=http://127.0.0.1:18081 \
OUT_DIR=/tmp/nexus_webcall_canary_http \
REQUIRE_CLEAN_WORKTREE=1 \
bash scripts/webcall_canary_readiness.sh
```

## Gates covered

The canary verifies:

1. Git safety and forbidden production file changes.
2. Backend WebCall tests:
   - voice API lifecycle;
   - LiveKit provider;
   - room compensation;
   - static headers;
   - mock/operator UI static tests;
   - canary static tests.
3. Frontend gates:
   - typecheck;
   - build;
   - unit tests.
4. Token and credential safety classification.
5. Click-to-accept static guarantee:
   - page load does not create local audio track;
   - session list does not create local audio track;
   - runtime config does not create local audio track;
   - local audio track is created only after operator accept path.
6. /webchat integrated entry static guarantee:
   - the main WebChat operator workspace renders `AgentWebCallPanel`;
   - conversation list shows an Incoming WebCall badge for tickets with ringing WebCall sessions;
   - `/webchat-voice` remains available as fallback.
7. WebCall Operational Queue tabs:
   - Incoming;
   - My Active;
   - All Active;
   - Missed;
   - Closed Recent.
8. Voice call evidence card:
   - WebChat thread displays status, voice_session_id, provider, accepted_by, ended_by, ringing_duration_seconds, talk_duration_seconds, total_duration_seconds, recording status, transcript status, and summary status;
   - ticket timeline receives the same `voice_call` payload.
9. Missed cleanup:
   - admin list queries clean expired ringing sessions to missed;
   - cleanup writes `voice.session.missed`;
   - cleanup writes final `voice_call` evidence.
10. Runtime config no-secret policy:
   - `/api/webchat/voice/runtime-config` may expose enabled/provider/livekit_url and capability booleans only;
   - it must not expose LiveKit API key, LiveKit API secret, participant token, visitor token, password, refresh token, or provider credentials.
11. Two-browser proof output:
   - Browser A visitor opens `/webcall/{voice_session_id}` and clicks Join;
   - Browser B operator opens `/webchat`, sees Incoming WebCall badge, accepts, ends, and continues text follow-up in the same ticket;
   - the same ticket shows the `voice_call` evidence card.
12. Optional HTTP readiness:
   - runtime config is reachable;
   - runtime config exposes only safe public runtime values;
   - /webchat integrated entry is reachable;
   - /webchat-voice fallback is reachable.

## Expected result

The final marker must be:

```text
CANARY_RESULT=PASS
```

Any failure blocks release promotion.
