# NexusDesk WebCall Canary Readiness Runbook

## Purpose

This runbook defines the pre-release canary gate for the WebCall operator console and voice lifecycle path after PR-B1 and PR-B2.

The canary is intentionally non-deploying. It validates readiness before any production rollout decision.

## Safety boundary

The canary must not:

- deploy production;
- run docker compose up;
- modify deploy/.env.prod;
- modify LiveKit provider credentials;
- modify database migrations;
- request microphone permission during page load;
- render provider credentials or voice participant credentials into operator UI.

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
6. Optional HTTP readiness:
   - runtime config is reachable;
   - runtime config exposes only safe public runtime values;
   - /webchat-voice is reachable.

## Expected result

The final marker must be:

```text
CANARY_RESULT=PASS
```

Any failure blocks release promotion.
