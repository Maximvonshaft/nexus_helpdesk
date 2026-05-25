# backend/app/services/AGENTS.md — Service Layer Execution Contract

This contract applies to `backend/app/services/**`. Services are business logic and runtime integration boundaries. Agents must treat this layer as production-critical: service changes can affect customer replies, queues, provider calls, storage, auth, audit, and external side effects.

## 1. Mandatory inspection before service changes

Before editing a service, inspect:

```text
calling API route in backend/app/api/**
backend/app/models.py
backend/app/settings.py
backend/app/unit_of_work.py
related backend/tests/test_*.py
related worker/daemon script when jobs are involved
webapp/src/lib/api.ts if operator console uses the behavior
```

If the service touches external side effects, also inspect docs/runbooks under:

```text
docs/architecture/**
docs/ops/**
docs/security/**
deploy/**
```

## 2. Service ownership map

| Domain | Primary files/patterns | Must preserve |
|---|---|---|
| Permissions | `permissions.py` | capability checks, tenant/operator authorization, admin boundaries |
| Background jobs | `background_jobs.py`, `run_worker.py` | durable queue, dedupe, retry, lock/lease, metrics, no in-request long side effects |
| Outbound messaging | `message_dispatch.py`, outbound services | queue-first dispatch, provider fallback, no direct customer send outside policy |
| WebChat Fast | `webchat_fast_*` services | rate limit, idempotency, server context, handoff policy, safe provider fallback |
| WebCall AI | `webcall_ai_production/**` | visitor-token/session safety, LiveKit/WebRTC boundary, handoff, transcript/event persistence |
| Provider Runtime | `provider_runtime/**`, `ai_runtime/**` | provider router, strict output contract, credential custody, kill switch, canary, fail-closed |
| OpenClaw | `openclaw_*`, bridge/client services | MCP primary path, event cursor, transcript sync, unresolved event idempotency, runtime health |
| Speedaf | `speedaf_*`, tracking fact services | redaction, capability gate, feature flags, durable work-order jobs |
| Storage/files | storage services | visibility checks, MIME/size/host/timeout guards, storage_key movement |
| Observability | `observability.py`, metrics services | request IDs, structured logs, redaction, metrics gates |

## 3. Transaction and side-effect rules

- Do not add scattered `db.commit()` calls unless matching an existing local pattern and documented in the PR.
- Prefer `managed_session()` at use-case boundaries.
- Use `db.flush()` when an ID is needed before a final outer commit.
- External side effects should usually be queued, not performed on the request path.
- If a side effect must run synchronously, enforce timeout, idempotency, error mapping, logging, and fallback.
- Never let provider/AI failure leave a partial customer-visible state without clear retry or fallback.

## 4. Provider Runtime / Codex service rules

Files of interest include:

```text
backend/app/services/provider_runtime/**
backend/app/services/ai_runtime/**
backend/app/services/webchat_fast_ai_service.py
```

Must preserve:

```text
reply-only Codex authority
strict output contract
provider router fallback order
kill_switch and canary_percent
credential encryption and custody
OAuth state/session safety
token redaction
no raw upstream payload echo
safe timeout/error mapping
```

Never add:

```text
Codex direct ticket mutation
Codex direct shell/file/browser operation
Codex direct customer send
Codex operational Speedaf action
raw token/log echo
fail-open provider behavior
```

## 5. OpenClaw service rules

Must preserve:

```text
MCP as primary production route
CLI fallback disabled in production unless recovery runbook explicitly permits it
conversation link uniqueness
transcript message uniqueness
attachment reference capture
sync cursor progression
unresolved event dedupe/replay/drop semantics
runtime heartbeat reporting
```

Any OpenClaw event-consumption change must verify:

```text
cursor update happens only after safe processing
unresolved event persistence works for unlinked sessions
replay is idempotent
drop is audited or intentionally recorded
sync daemon and event daemon remain observable
```

## 6. WebChat Fast service rules

Must preserve:

```text
server-owned context over frontend-only context
request hash/idempotency behavior
tracking fact redaction before prompt/customer reply
support-hours deterministic response
server handoff policy
Speedaf work-order enqueue only when gated and justified
stream and non-stream semantic parity
```

If changing streaming behavior, compare `/fast-reply` and `/fast-reply/stream` paths for parity.

## 7. WebCall service rules

Must preserve:

```text
public_id not raw DB id as customer token surface
visitor token checks
join token authority and expiry
handoff event semantics
tracking fallback capture
session end idempotency/safety
recording/transcription/AI-agent feature flags
```

Do not expose demo/sandbox service behavior to public customer routes unless explicitly feature-gated and tested.

## 8. Speedaf service rules

Any Speedaf external action must have:

```text
feature flag
capability check
operator/tenant authorization
idempotency or dedupe key
audit/event log
safe retry semantics
PII redaction in prompts/logs
rollback or compensation note
```

Tracking fact lookup must never leak full PII into customer-visible payload or AI prompt unless explicitly classified safe and redacted.

## 9. Storage and attachment rules

Remote attachment fetch must enforce:

```text
allowed host list
scheme restriction
byte limit
timeout
MIME allowlist
file extension policy
redaction/log safety
storage backend abstraction
```

Do not expose raw `file_path`. Preserve ticket visibility checks.

## 10. Required validation

For service changes:

```bash
set -Eeuo pipefail
PYTHONPATH=backend python -m compileall backend/app backend/scripts
PYTHONPATH=backend pytest -q <targeted service tests>
```

Broaden to full backend suite when touching:

```text
auth/permissions
provider runtime/Codex
OpenClaw
WebChat/WebCall
Speedaf actions
storage/files
background jobs/worker
settings/production guards
```
