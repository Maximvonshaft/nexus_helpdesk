# AGENTS.md — NexusDesk / Nexus Helpdesk Single-File Execution Blueprint v3

This is the single source of truth for AI coding agents working in `Maximvonshaft/nexus_helpdesk`.

Do not add subdirectory `AGENTS.md` files unless the repository owner explicitly requests a multi-file policy. This project intentionally uses one root `AGENTS.md` to reduce drift, conflicts, duplicated rules, and maintenance overhead.

This document is not a generic style guide. It is a minimum-granularity construction blueprint for repository work: what to inspect, what to change, what not to touch, what tests to run, what evidence to report, where to stop, and how to hand off work to another agent or human reviewer.

---

## 1. Binding order

Follow instructions in this order:

```text
1. system / platform policy
2. explicit user task
3. this AGENTS.md
4. existing repository code, tests, docs, CI, and deployment files
5. external official documentation only when current behavior or library/runtime semantics may have changed
```

If repository code conflicts with this file, do not guess. Report the conflict with exact file paths and propose the least-risk resolution.

---

## 2. Project mission

NexusDesk / Nexus Helpdesk is a case-centric customer operations runtime for logistics support and exception handling. It combines:

```text
customer conversations
tickets and case workspace
manual operator workflows
AI-assisted intake and replies
attachments and evidence
outbound replies
audit history
WebChat
WebCall / voice
OpenClaw routing and transcript sync
Codex provider runtime
Speedaf operational actions
provider credential custody
runtime health and production sign-off
```

The production objective is controlled customer-support automation. AI providers, OpenClaw, Codex, WebChat, WebCall, and Speedaf adapters must stay behind NexusDesk policy gates, audit trails, permissions, observability, feature flags, tests, and rollback controls.

---

## 3. Single-file management policy

This repository should have exactly one agent instruction file:

```text
AGENTS.md
```

Do not create:

```text
backend/app/api/AGENTS.md
backend/app/services/AGENTS.md
webapp/AGENTS.md
tools/**/AGENTS.md
deploy/AGENTS.md
docs/**/AGENTS.md
```

When a new domain needs rules, add a section to this root file. Keep sections path-addressable and concrete.

---

## 4. Evidence classification

Every audit, implementation plan, PR summary, or handoff must separate facts from speculation.

Use these labels:

```text
FACT        Directly verified from code, test, log, command output, CI, or official docs.
INFERENCE   Derived from verified facts; explain the reasoning.
ASSUMPTION  Temporary working assumption; mark it as unverified.
UNKNOWN     Missing evidence; list what must be inspected or run.
RISK        Failure mode, security issue, data risk, business impact, or operational risk.
VERIFY      Exact next command, file, endpoint, test, or log needed to confirm.
```

Never present an assumption as fact.

---

## 5. Repository source map

```text
backend/app/main.py                         FastAPI app assembly, middleware, security headers, router registration, health/readiness/metrics, SPA fallback
backend/app/settings.py                     runtime configuration, production guards, env validation
backend/app/models.py                       SQLAlchemy domain model, tables, relations, indexes
backend/app/enums.py                        domain enums used by models/routes/services
backend/app/unit_of_work.py                 managed transaction boundaries
backend/app/api/                            HTTP route surface
backend/app/services/                       business logic, provider runtime, storage, permissions, jobs, OpenClaw, WebChat, WebCall, Speedaf
backend/app/static/webchat/                 embeddable WebChat/WebCall static customer-facing assets
backend/alembic/                            Alembic migration environment and revisions
backend/scripts/                            worker, daemon, smoke, probe, validation, runtime scripts
backend/tests/                              backend regression, contract, security, runtime, WebChat/WebCall tests

webapp/                                     modern React / TypeScript / Vite operator console
webapp/src/main.tsx                         React root, QueryClientProvider, RouterProvider, web vitals
webapp/src/router.tsx                       frontend route tree
webapp/src/lib/api.ts                       frontend API client and backend contract map
webapp/src/lib/types.ts                     typed API models
webapp/src/routes/**                        page routes
webapp/src/components/**                    reusable UI and domain components
webapp/tests/**                             Node tests / contract tests
frontend/                                   legacy static fallback only
frontend_dist/                              generated SPA build output; never edit or commit as source

tools/nexus-codex-runtime/                  Node/TypeScript Codex app-server runtime sidecar
tools/**                                   other tooling/probes/runtimes if added later

Dockerfile                                  multi-stage image build for webapp, Codex runtime, OpenClaw runtime, Python backend
deploy/                                     deployment templates, proxy scripts, compose, nginx/systemd samples
scripts/deploy/                             deployment preflight, migrations, backup, restore helpers
scripts/**                                  repository probes/utilities
.github/workflows/                          CI definitions and PR guards
docs/architecture/                          architecture records
docs/ops/                                   operational runbooks
docs/security/                              security boundaries and risk records
docs/engineering/                           execution packs, implementation plans, technical handoffs if present
```

---

## 6. Universal workflow for every task

Do not start by patching. Start by building an evidence map.

```text
Step 1 — Classify the change
  docs-only | backend model | backend API | backend service | backend script | DB migration | webapp UI | static widget | WebChat | WebCall | OpenClaw | Codex provider runtime | Speedaf action | storage/attachment | deployment | CI | tests | runbook | architecture/security docs

Step 2 — Identify impacted contracts
  routes | services | models/tables | migrations | frontend API client | frontend routes | workers/daemons | env vars | tests | deployment files | docs/runbooks | CI gates | rollback path

Step 3 — Inspect exact files before editing
  Never rely on filenames alone. Read the route, service, model, settings, migration, tests, workflow, and deployment file for the touched area.

Step 4 — Patch narrowly
  Preserve behavior, data compatibility, permissions, idempotency, rate limits, audit logs, feature flags, observability, and rollback.

Step 5 — Validate by change type
  Run the smallest relevant tests first, then broaden when the area is public-facing, security-sensitive, queue-related, provider-related, migration-related, or customer-facing.

Step 6 — Report evidence
  Include facts, files, functions/routes/classes/tables, commands, outputs, skipped checks, risk, rollback, and remaining unknowns.
```

---

## 7. Change-type execution matrix

| Change type | Required inspection | Required validation | Hard stop conditions |
|---|---|---|---|
| Docs-only | Existing docs, README, relevant code path if making technical claims | Markdown review; no secrets; no generated/runtime artifacts | Any unverified technical claim |
| Backend model | `backend/app/models.py`, `enums.py`, service/API users, migration chain, tests | compileall; Alembic check if schema changed; targeted tests | Model change without migration; unsafe nullable/default/backfill; broken relationship/cascade |
| Backend route | `backend/app/main.py`, target `backend/app/api/*.py`, deps/auth, service, model/table, tests | compileall; targeted pytest | Missing permission check, missing idempotency on writes, route not registered, public API without rate/origin policy |
| Backend service | Calling API route, target service, models, settings, job/worker path, tests | compileall; targeted pytest; broaden if security/queue/provider | Hidden scattered commits; bypasses audit/policy gate; external side effect without timeout/fallback |
| Backend script | target script, service it calls, settings/env, deployment command, worker/daemon tests | compileall; script help/dry-run if available; targeted tests | Destructive default; no exit-code discipline; no logs; unsafe live side effect |
| DB migration | `models.py`, `backend/alembic/env.py`, current heads/history, related service/tests | `alembic heads`, `history`, `upgrade head`; targeted tests | Editing applied migration without explicit repair context; non-reversible migration without rollback note |
| Webapp UI | `router.tsx`, `api.ts`, `types.ts`, relevant route/component, backend contract, tests | `npm ci`, lint, typecheck, test, build; e2e for behavior | Fake control; auth token moved to localStorage; response shape guessed |
| Static widget | `backend/app/static/webchat/**`, backend webchat/voice routes, CSP/permissions headers, widget tests | static/header/widget tests; manual asset smoke where feasible | Customer-facing break; unsafe endpoint; stale cache/CORS/voice redirect issue |
| WebChat fast path | `webchat_fast.py`, `webchat_fast_*` services, provider router, rate/idempotency tests, static widget if touched | WebChat backend test group; stream tests if `/fast-reply/stream` touched | Origin/rate/idempotency bypass; unsafe AI fallback; customer reply outside policy gate |
| WebCall / voice | `webcall_ai.py`, `admin_webcall_ai.py`, `webcall_ai_production/**`, voice config, webapp WebCall routes/components, static voice assets, PR WebCall guard | WebCall backend tests; webapp checks; Playwright where UI changed | Mic/camera permission outside intended path; demo exposed as production; no provider fallback |
| Provider Runtime / Codex | `admin_provider_runtime.py`, `admin_provider_credentials.py`, `provider_runtime/**`, `ai_runtime/**`, `tools/nexus-codex-runtime/**`, deploy Codex proxies, provider tests | Codex runtime `npm test`; provider-runtime pytest; WebChat provider tests | Codex gets direct ticket/file/shell/customer-send authority; secrets exposed; parser weakened; fail-open behavior |
| OpenClaw | bridge/client/sync/event services, `OpenClaw*` models, daemon scripts, runtime-health APIs, unresolved-event tests | OpenClaw runtime/observability tests; daemon readiness tests | Direct send outside audit/policy; cursor/idempotency broken; CLI fallback enabled in production without runbook |
| Speedaf action | `speedaf_actions`, `speedaf_cancel`, Speedaf services, background jobs/worker, audit, feature flags, tests | Speedaf action tests plus queue/job tests | Operational write enabled without capability, idempotency, audit, feature flag, rollback/compensation |
| Storage / attachment | `files` API, storage services, `TicketAttachment`, `OpenClawAttachmentReference`, settings, upload tests | file/security tests; storage readiness checks | Raw file path exposed; MIME/size/host timeout guard weakened; ticket visibility bypass |
| Deployment | `Dockerfile`, `deploy/**`, `scripts/deploy/**`, settings production guards, README deployment notes | compose build where feasible; migration command; healthz/readyz; no live deploy without approval | Overwriting live `.env.prod`, `data/`, uploads, token files; destructive server cleanup |
| CI/workflows | target workflow, invoked tests, package/requirements, path filters, permissions, concurrency | YAML sanity; reason through triggers/paths; run affected commands when possible | Removing security/test gate to pass PR; adding write permissions without need |
| Tests | tested code path, existing related tests, CI grouping | targeted test; full suite if P0/P1 path | Deleting/weaking test without replacement; mock hides actual boundary |
| Runbook | code path, deploy path, secret path, rollback path | command sanity; no secrets; expected output/failure classification | Vague steps; missing rollback; real secret/token in docs |
| Architecture/security docs | code paths and threat model | cite exact code paths; no speculative claims as facts | Conceptual claims without code anchors; unsafe authority expansion |

---

## 8. P0/P1 path map

Use this to determine review and validation depth.

| Path pattern | Domain | Risk | Required posture |
|---|---|---|---|
| `backend/app/main.py` | app assembly/security headers/router/static fallback | P0 | inspect full request/security impact |
| `backend/app/settings.py` | runtime config/production guards/secrets | P0 | fail-closed, no weak production defaults |
| `backend/app/models.py` | data model | P0 | migration/backfill/index/compatibility required |
| `backend/alembic/**` | schema migrations | P0 | immutable explicit migrations, upgrade validation |
| `backend/app/api/webchat_fast.py` | public WebChat customer entry | P0 | origin/rate/idempotency/provider fallback tests |
| `backend/app/api/webcall_ai.py` | public WebCall AI session entry | P0 | token/session/voice lifecycle tests |
| `backend/app/api/admin_provider_credentials.py` | Codex credential custody | P0 | no token echo; admin/capability checks |
| `backend/app/api/admin_provider_runtime.py` | provider routing | P0 | kill switch/canary/fallback checks |
| `backend/app/services/provider_runtime/**` | provider routing/adapters | P0 | strict output/fail-closed/secret redaction |
| `backend/app/services/ai_runtime/**` | AI provider boundary | P0 | reply-only, no autonomous action |
| `backend/app/services/webcall_ai_production/**` | production WebCall service | P0 | session/token/room/event/handoff tests |
| `backend/app/services/webchat_fast_*` | WebChat fast business logic | P0 | non-stream/stream parity and safe fallback |
| `backend/app/services/openclaw*` | OpenClaw bridge/sync | P0 | idempotency/cursor/runtime health |
| `backend/app/services/speedaf*` | Speedaf operational actions | P0 | feature flag/capability/audit/idempotency |
| `backend/app/static/webchat/**` | embeddable customer assets | P0 | no broken customer-facing entry |
| `backend/scripts/run_worker.py` | background jobs/outbound | P0 | durable locks/retries/metrics |
| `backend/scripts/run_openclaw_*` | OpenClaw daemons | P0 | heartbeat/cursor/retry safety |
| `webapp/src/lib/api.ts` | frontend API/auth boundary | P0 | sessionStorage, request IDs, timeout, no unsafe retry |
| `webapp/src/router.tsx` | route exposure | P1 | internal/demo route visibility controlled |
| `webapp/src/routes/webcall*` | WebCall frontend | P0 | mic cleanup and provider fallback |
| `tools/nexus-codex-runtime/**` | Codex sidecar | P0 | strict parser, redaction, timeout, queue |
| `Dockerfile` | production image | P0 | deterministic copy, no secrets, non-root runtime |
| `deploy/**` | deployment templates/proxies | P0 | no secrets/live state, safe defaults |
| `scripts/deploy/**` | deploy/migration/backup helpers | P0 | backup first, no destructive default |
| `.github/workflows/**` | quality gates | P1 | do not weaken CI |
| `docs/security/**` | security truth | P1 | threat model and code anchors required |
| `docs/architecture/**` | architecture truth | P1 | code-path evidence required |
| `docs/ops/**` | runbooks | P1 | executable, rollback-ready |

---

## 9. Non-negotiable global rules

1. Inspect before modifying. Read exact routes, services, models, migrations, tests, workflows, and deployment files.
2. Base conclusions on repository files, commands, tests, logs, CI, or reproducible output.
3. Prefer surgical patches. Preserve public contracts unless an approved task explicitly changes them.
4. Preserve backward compatibility for API contracts, database data, migrations, UI workflows, and deployment defaults.
5. Fail closed for AI, provider runtime, credentials, outbound messaging, Speedaf actions, WebCall, WebChat, authentication, storage, and metrics.
6. Never commit secrets, real `.env` files, tokens, passwords, cookies, session dumps, private gateway URLs, private IPs, Tailscale addresses, customer PII, or local runtime artifacts.
7. Never run destructive production commands from an agent session: no live `git reset --hard`, no `rm -rf data`, no unreviewed database drop/downgrade, no direct traffic switch, no unapproved deploy.
8. Do not send real customer outbound messages, perform Speedaf writes, modify provider credentials, trigger refunds/claims/address changes, or operate OpenClaw accounts during tests unless the user explicitly authorizes that exact action.
9. Any mock, placeholder, fake button, or unconnected UI must be labeled as such in code and PR. Production-facing UI must call real APIs.
10. Every PR must include validation evidence and rollback.
11. Do not remove tests to make PRs pass. Replace with equivalent or stronger tests if restructuring.
12. Do not touch `frontend_dist/` as source.
13. Do not weaken rate limits, CORS/origin checks, CSP, permission policy, idempotency, audit, or auth for debugging.
14. Do not log authorization responses, tokens, cookies, raw upstream payloads, or unredacted PII.
15. Do not expose demo or sandbox UI as production customer flow without explicit feature flag, tests, and approval.

---

## 10. Backend app/model/settings contract

### `backend/app/main.py`

Before changing app assembly, inspect:

```text
router imports and app.include_router order
CORS middleware
request_context_middleware()
security headers
DEFAULT_CSP / voice CSP
Permissions-Policy logic
/healthz
/readyz
/metrics
webchat static mounts
SPA fallback
```

Preserve:

```text
request ID propagation
X-Content-Type-Options
X-Frame-Options
Referrer-Policy
Content-Security-Policy
Permissions-Policy
Cache-Control: no-store for /api/**
metrics token gate
storage readiness in /readyz
frontend_dist fallback behavior
webchat static path isolation
```

### `backend/app/settings.py`

Before changing settings, inspect production normalization and all related tests.

Hard stops:

```text
production without SECRET_KEY
production without PostgreSQL
AUTO_INIT_DB / SEED_DEMO_DATA enabled in production
ALLOW_DEV_AUTH enabled in production
legacy integration API key enabled in production
OpenClaw CLI fallback enabled in production
localhost origins allowed in production
metrics enabled without METRICS_TOKEN
legacy WebChat token transport enabled in production
remote attachment fetch enabled without allowed hosts
```

### `backend/app/models.py`

Any model change must answer:

```text
Which table changes?
Which API/service reads/writes it?
Is Alembic migration required?
Is backfill required?
Is column nullable/default safe for existing rows?
Are indexes needed for pagination/queue/idempotency/search?
Are relationships and cascade behavior safe?
Are enum values backward compatible?
Which tests validate old and new behavior?
```

High-risk table families:

```text
users / auth_throttle_entries / user_capability_overrides
admin_audit_logs / admin_action_rate_limits
integration_clients / integration_request_logs
markets / channel_accounts / market_bulletins
ai_config_resources / ai_config_versions
customers / tickets / ticket_comments / ticket_events / ticket_attachments / ticket_outbound_messages / ticket_ai_intakes
background_jobs
openclaw_conversation_links / openclaw_transcript_messages / openclaw_attachment_references / openclaw_sync_cursors / openclaw_unresolved_events
webchat_rate_limits
provider runtime / credential custody tables
webcall / voice session tables if present
Speedaf action/work-order tables if present
```

---

## 11. Backend API route contract

API routes define auth, authorization, idempotency, rate limits, request/response schema, and customer-visible behavior.

Mandatory inspection for any API change:

```text
backend/app/main.py
backend/app/settings.py
backend/app/api/deps.py
backend/app/services/permissions.py
backend/app/models.py
calling/called service files
backend/tests relevant to route
webapp/src/lib/api.ts if operator console uses route
webapp/src/lib/types.ts if response shape changes
```

### Route class gates

| Route class | Required gate |
|---|---|
| `/api/admin/**` | `get_current_user` plus capability/admin check |
| Operator ticket routes | current user plus ticket/customer/team visibility |
| Integration routes | integration client auth, scope, rate limit, idempotency where write-like |
| Public WebChat routes | origin validation, rate limit, idempotency, no-store, safe schema |
| Public WebCall/voice routes | visitor/session token checks, feature flags, voice runtime controls |
| Files/download routes | authenticated access plus ticket visibility |
| Metrics route | token-gated when enabled |

### High-risk API files

#### `backend/app/api/webchat_fast.py`

Critical contracts:

```text
POST /api/webchat/fast-reply
POST /api/webchat/fast-reply/stream
OPTIONS /api/webchat/fast-reply
OPTIONS /api/webchat/fast-reply/stream
_validated_origin()
_public_cors_headers()
enforce_webchat_fast_rate_limit()
begin_webchat_fast_idempotency()
compute_request_hash()
compute_legacy_v1_request_hash_aliases()
get_or_create_fast_conversation()
append_fast_visitor_message()
extract_fast_business_state()
resolve_fast_routing_context()
decide_server_handoff_policy()
_lookup_fast_tracking_fact()
_tracking_fact_forced_reply_payload()
generate_webchat_fast_reply()
mark_webchat_fast_done()/mark_webchat_fast_failed()
```

Hard stops:

```text
No origin bypass in production.
No rate-limit bypass.
No idempotency bypass.
No unredacted tracking fact in customer-visible payload or prompt.
No unsafe AI/provider fallback.
No customer reply outside NexusDesk policy/audit path.
Stream and non-stream semantics must stay aligned.
```

#### `backend/app/api/webcall_ai.py`

Critical contracts:

```text
GET  /api/webcall-ai/runtime-config
POST /api/webcall-ai/sessions
GET  /api/webcall-ai/sessions/{session_public_id}
POST /api/webcall-ai/sessions/{session_public_id}/join-token
POST /api/webcall-ai/sessions/{session_public_id}/end
POST /api/webcall-ai/sessions/{session_public_id}/handoff
POST /api/webcall-ai/sessions/{session_public_id}/tracking-fallback
GET  /api/webcall-ai/sessions/{session_public_id}/events
```

Preserve:

```text
visitor token validation
Idempotency-Key handling on session creation
managed_session() write boundaries
handoff safety path
tracking fallback storage
session event visibility
runtime config not exposing secrets
voice path permissions policy in backend/app/main.py
```

#### `backend/app/api/admin_provider_runtime.py`

Critical contracts:

```text
GET   /api/admin/provider-runtime/status
PATCH /api/admin/provider-runtime/routing/webchat-fast-reply
ensure_can_manage_runtime()
primary_provider allowlist
fallback_provider allowlist
codex requires OpenClaw fallback
kill_switch
enabled
canary_percent
timeout_ms
```

#### `backend/app/api/admin_provider_credentials.py`

Critical contracts:

```text
GET   /api/admin/provider-credentials/codex/status
POST  /api/admin/provider-credentials/codex/smoke-chat
POST  /api/admin/provider-credentials/codex/authorize
POST  /api/admin/provider-credentials/codex/manual/start
POST  /api/admin/provider-credentials/codex/manual/complete
GET   /api/admin/provider-credentials/codex/callback
POST  /api/admin/provider-credentials/codex/device/start
GET   /api/admin/provider-credentials/codex/device/status/{session_id}
POST  /api/admin/provider-credentials/codex/device/poll/{session_id}
POST  /api/admin/provider-credentials/codex/refresh/{credential_id}
POST  /api/admin/provider-credentials/codex/revoke/{credential_id}
POST  /api/admin/provider-credentials/codex/disconnect/{credential_id}
```

Preserve:

```text
runtime-management capability gate
admin-only smoke chat gate
OAuth callback high-entropy state handling
no token echo in response/logs
credential encryption/custody
refresh/revoke/disconnect audit behavior
```

#### Speedaf API routes

Files:

```text
backend/app/api/speedaf_actions.py
backend/app/api/speedaf_cancel.py
```

Do not enable write actions unless all are present:

```text
capability check
tenant/operator authorization
idempotency key or durable dedupe
audit log
feature flag default safe/off when appropriate
background job path when external side effect is async
rollback or compensation note
test coverage
```

#### Files / attachments API

Must preserve:

```text
authenticated access
ticket visibility before download
no raw local file path exposure
MIME/extension/size safety
storage backend abstraction
```

#### Auth / admin / users / queues / tickets / lite / integration / customers / lookups / outbound / persona / knowledge / stats

When touching these route families, map route to:

```text
request schema
response schema
service function
model/table
permission/capability
idempotency if write-like
pagination/cursor behavior if list endpoint
frontend api.ts method if used
backend tests
rollback behavior
```

Do not change response shape silently. If a frontend route uses the response, update all of:

```text
backend route/schema
backend tests
webapp/src/lib/types.ts
webapp/src/lib/api.ts
webapp route/component
webapp tests if present
```

---

## 12. Backend service contract

Services are production-critical because they contain side effects, provider calls, queues, storage, permissions, and customer-visible business logic.

Mandatory inspection before service changes:

```text
calling API route
backend/app/models.py
backend/app/settings.py
backend/app/unit_of_work.py
related tests
worker/daemon script when jobs are involved
webapp/src/lib/api.ts if operator console uses behavior
docs/architecture, docs/ops, docs/security when external boundary is involved
```

### Service ownership map

| Domain | Primary files/patterns | Must preserve |
|---|---|---|
| Permissions | `permissions.py` | capability checks, tenant/operator authorization, admin boundaries |
| Background jobs | `background_jobs.py`, `run_worker.py` | durable queue, dedupe, retry, lock/lease, metrics |
| Outbound messaging | `message_dispatch.py`, outbound services | queue-first dispatch, provider fallback, no direct customer send outside policy |
| WebChat Fast | `webchat_fast_*` services | rate limit, idempotency, server context, handoff policy, safe provider fallback |
| WebCall AI | `webcall_ai_production/**` | visitor/session safety, room/token boundary, handoff, event persistence |
| Provider Runtime | `provider_runtime/**`, `ai_runtime/**` | provider router, strict output, credential custody, kill switch, canary, fail-closed |
| OpenClaw | `openclaw_*`, bridge/client services | MCP primary path, event cursor, transcript sync, unresolved event idempotency, runtime health |
| Speedaf | `speedaf_*`, tracking fact services | redaction, capability gate, feature flags, durable jobs |
| Storage/files | storage services | visibility checks, MIME/size/host/timeout guards, storage_key movement |
| Observability | `observability.py`, metrics services | request IDs, structured logs, redaction, metrics gates |

### Transaction and side-effect rules

```text
Prefer managed_session() at use-case boundaries.
Use db.flush() when ID is needed before final outer commit.
Avoid scattered db.commit() inside services unless matching existing pattern and explained in PR.
External side effects should usually be queued, not done on request path.
Synchronous external call requires timeout, idempotency, error mapping, logging, and fallback.
Provider/AI failure must not leave partial customer-visible state without retry/fallback.
```

### Provider runtime / Codex services

Files/patterns:

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

### WebCall production services

Files/patterns:

```text
backend/app/services/webcall_ai_production/**
backend/app/webcall_ai_schemas.py
```

Must preserve:

```text
public_id not raw DB id as customer token surface
visitor token checks
join token authority and expiry
session lifecycle status transitions
handoff event semantics
tracking fallback capture
session end idempotency/safety
recording/transcription/AI-agent feature flags
room/provider cleanup on failure
no demo behavior in production path
```

### WebChat Fast services

Files/patterns:

```text
backend/app/services/webchat_fast_ai_service.py
backend/app/services/webchat_fast_session_service.py
backend/app/services/webchat_fast_rate_limit.py
backend/app/services/webchat_fast_idempotency_db.py
backend/app/services/webchat_fast_stream_service.py
backend/app/services/webchat_handoff_policy.py
backend/app/services/webchat_handoff_policy_config.py
backend/app/services/tracking_fact_service.py
backend/app/services/tracking_fact_schema.py
```

Must preserve:

```text
server-owned context over frontend-only context
request hash/idempotency behavior
rate limit with production database backend when configured
tracking fact redaction before prompt/customer reply
support-hours deterministic response
server handoff policy
Speedaf work-order enqueue only when gated and justified
stream and non-stream semantic parity
```

### OpenClaw services

Must preserve:

```text
MCP as primary production route
CLI fallback disabled in production unless recovery runbook permits it
conversation link uniqueness
transcript message uniqueness
attachment reference capture
sync cursor progression
unresolved event dedupe/replay/drop semantics
runtime heartbeat reporting
```

Any event-consumption change must verify:

```text
cursor update happens only after safe processing
unresolved event persistence works for unlinked sessions
replay is idempotent
drop is audited or intentionally recorded
sync daemon and event daemon remain observable
```

### Speedaf services

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

### Storage and attachments

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

---

## 13. Backend scripts contract

Applies to:

```text
backend/scripts/**
scripts/**
scripts/deploy/**
```

Scripts can affect production state. Default must be safe, deterministic, logged, and repeatable.

Before changing scripts, inspect:

```text
services the script calls
settings/env variables
Docker/compose/systemd command using script
CI workflow using script
tests covering script or related service
runbook referencing script
```

Script rules:

```text
Use explicit exit codes.
Use clear stdout/stderr messages.
Do not require interactive input unless the runbook says so.
Support dry-run/read-only mode for risky operations when practical.
Do not hardcode secrets, private IPs, real tokens, or production-only paths.
Do not delete or mutate data by default.
For workers/daemons, preserve worker-id, heartbeat, lock/lease, retry, and graceful shutdown behavior.
For deploy scripts, backup before migration/restore/destructive operations.
For probes, write outputs to a timestamped OUT directory when possible.
```

Worker/daemon scripts must preserve:

```text
bounded polling intervals
lock/lease semantics
retry/max attempts
heartbeat/runtime health
structured logs or clear event logs
safe handling of provider unavailable
safe SIGTERM/SIGINT behavior where implemented
```

---

## 14. Static WebChat/WebCall asset contract

Applies to:

```text
backend/app/static/webchat/**
```

These files can be customer-facing and embeddable. Treat them as production entrypoints.

Before changing static assets, inspect:

```text
backend/app/main.py static mounts
backend/app/api/webchat_fast.py
backend/app/api/webchat.py
backend/app/api/webchat_events.py
backend/app/api/webchat_voice.py
backend/app/webchat_voice_config.py
backend/tests/test_webchat_voice_static_headers.py
backend/tests/test_webchat_voice_mock_ui_static.py
widget/demo docs if present
```

Preserve:

```text
correct API paths
CORS/origin compatibility
no embedded secrets
cache behavior compatible with deployment
voice redirect behavior
no broken customer-facing demo flow
no direct provider secrets or internal admin API calls
```

Hard stops:

```text
Do not bypass backend rate/idempotency/origin controls in widget code.
Do not expose admin-only endpoints to public widget.
Do not require customer browser permissions before clear user action.
Do not add third-party scripts without security review.
```

---

## 15. Webapp operator console contract

`webapp/` is the modern frontend source of truth. `frontend_dist/` is generated output and must not be edited.

Current stack:

```text
React 18.3.1
TypeScript
Vite
TanStack Router
TanStack Query
Tailwind CSS v4
Radix UI primitives
Playwright
```

Mandatory inspection before UI changes:

```text
webapp/src/router.tsx
webapp/src/lib/api.ts
webapp/src/lib/types.ts
relevant webapp/src/routes/<route>.tsx
relevant webapp/src/components/**
matching backend route under backend/app/api/**
matching backend schema/service/model when response shape changes
webapp/tests/** if behavior is covered
```

Route tree includes:

```text
/login
/admin
/
/workspace
/webchat
/webchat-voice
/webcall
/webcall-ai
/webcall-ai-demo
/provider-credentials
/bulletins
/ai-control
/control-plane
/accounts
/users
/runtime
```

### API client hard rules

`webapp/src/lib/api.ts` owns API base normalization, request ID header, auth token handling, timeout, retry, and error mapping.

Preserve:

```text
normalizeApiBaseUrl()
buildApiUrl()
PUBLIC_API_PATHS
SAFE_RETRY_METHODS
REQUEST_ID_HEADER = X-Request-Id
getToken()/setToken()/clearToken()
sessionStorage token custody
AuthExpiredError behavior
ApiError behavior
fetchWithTimeout()
frontend latency event: nexusdesk:api-latency
```

Hard stops:

```text
Do not move auth token storage from sessionStorage to localStorage.
Do not add Authorization header to public endpoints.
Do not retry unsafe write methods by default.
Do not remove request ID propagation.
Do not silently swallow 401.
Do not introduce /api/api path duplication.
```

### UI production bar

Every operator-facing workflow must include:

```text
loading state
empty state
error state
permission/disabled state when action is unavailable
success confirmation for write actions
clear destructive-action affordance
keyboard-reachable controls
labels for non-icon-only actions
```

No fake buttons. A control must either:

```text
call a real API;
be disabled with a reason;
be hidden until supported;
or be explicitly marked demo/internal.
```

### Feature-specific frontend contracts

Case workspace/tickets must preserve:

```text
casesPage(), caseDetail(), ticketTimeline(), outbound capabilities, sendOutboundMessage(), workflowUpdate(), aiIntake()
pagination/cursor support
status/priority/team/assignee filters
outbound capability checks before send
human-readable loading/error/empty states
```

Bulletins/AI/persona/knowledge must preserve:

```text
published vs draft distinction
publish/rollback semantics
market-scoped context correctness
```

Channel/runtime/OpenClaw must preserve:

```text
runtimeHealth(), openclawConnectivityCheck(), consumeOpenClawEventsOnce(), unresolvedEvents(), replay/drop actions
visible confirmation for replay/drop
failed/degraded daemon visibility
```

Provider credentials/Codex must preserve:

```text
no token rendering
no authorization_response logging
no browser storage of authorization response
smoke chat not presented as production customer flow
```

WebChat operator must preserve:

```text
thread/event polling safety
fact-evidence confirmation fields
operator review before customer reply
clear error states for failed send/reply
```

WebCall/voice must preserve:

```text
no microphone acquisition without visible user/operator action
no active tracks left after reject/end/non-LiveKit fallback
demo-only WebCall AI not exposed as production
provider/livekit failure shown as actionable degraded state
```

---

## 16. Codex runtime sidecar contract

Applies to:

```text
tools/nexus-codex-runtime/**
tools/** when adding other runtimes/probes
```

Current sidecar routes:

```text
GET  /healthz
GET  /readyz
POST /reply
```

Core implementation anchors:

```text
src/server.ts              HTTP server, routes, semaphore, body limit, headers, redaction
src/env.ts                 runtime config and env loading
src/client-cache.ts        Codex appserver client cache and login state
src/account-login.ts       account login flow
src/deadline.ts            request deadline handling
src/metrics.ts             stage timing
src/reply-contract.ts      request validation and strict reply parsing
src/redaction.ts           response/log redaction
src/thread-runner.ts       ephemeral thread execution
test/*.test.ts             runtime contract tests
```

Authority boundary:

```text
The sidecar may return a structured reply to NexusDesk.
It must not modify tickets, send customer messages, execute shell commands, write files, read cookies/browser sessions, run model-native tools, perform Speedaf actions, or expose tokens/raw upstream payloads.
```

Preserve in `/reply`:

```text
config.enabled fail-closed check
request body size limit
validateReplyRequest()
clientCacheKey()
cache.getOrCreate()
cache.ensureLoggedIn()
loginFingerprint()
runEphemeralThread()
parseStrictReply()
redact() before response
StageTimer stage_ms
Semaphore maxConcurrency and queue timeout
X-Nexus-Codex-* diagnostic headers
no-store JSON responses
```

Do not weaken strict reply parsing. Invalid upstream assistant output must not pass through as customer reply.

Required headers unless changed with tests:

```text
X-Nexus-Codex-Backend
X-Nexus-Codex-Elapsed-Ms
X-Nexus-Codex-Client-Cache
X-Nexus-Codex-Login
X-Nexus-Codex-Thread-Mode
X-Nexus-Codex-Upstream-SHA
```

---

## 17. Database and Alembic contract

Every persistent model/table/index/constraint change requires an Alembic revision.

Before adding/editing migration, inspect:

```text
backend/app/models.py
backend/app/enums.py
backend/alembic/env.py
backend/alembic/versions/**
backend/app/settings.py
backend/tests/** touching affected model/table
service/API path that reads/writes table
```

Migration rules:

```text
Do not use runtime create_all/drop_all semantics for production schema changes.
Do not silently edit old applied migrations.
Revision IDs must fit alembic_version.version_num and not repeat.
Prefer explicit DDL operations.
Include indexes for queue, lookup, pagination, idempotency, and high-cardinality access paths.
If downgrade is unsafe, document operational rollback.
```

Adding non-null column to populated table:

```text
1. add nullable column or server_default
2. backfill safely if needed
3. enforce non-null later only when safe
```

Required validation:

```bash
set -Eeuo pipefail
cd backend
PYTHONPATH=. alembic heads
PYTHONPATH=. alembic history --verbose
PYTHONPATH=. alembic upgrade head
```

If migration depends on PostgreSQL-specific behavior such as partial indexes or `FOR UPDATE SKIP LOCKED`, validate against PostgreSQL, not only SQLite.

---

## 18. Deployment and operations contract

Applies to:

```text
Dockerfile
deploy/**
scripts/deploy/**
docs/ops/**
```

Live-state separation. Never commit or overwrite:

```text
deploy/.env.prod
real secrets
/run/secrets content
data/
uploads / local storage roots
server-only compose override files
private Nginx/TLS files
private token files
Tailscale addresses or private gateway URLs
```

### Dockerfile rules

The Dockerfile builds:

```text
webapp builder
nexus-codex-runtime builder
openclaw runtime
Python backend runtime
```

Do not regress:

```text
webapp build from source
nexus-codex-runtime npm build
OpenClaw/Codex availability checks
backend requirements install
copy only deterministic source paths
frontend_dist generated inside image
non-root appuser runtime
healthcheck on /healthz
```

Do not `COPY . .` into the image.

### Compose rules

Preserve:

```text
PostgreSQL healthcheck
app bound to 127.0.0.1 host port by default
APP_ENV=production
AUTO_INIT_DB=false
SEED_DEMO_DATA=false
OPENCLAW_CLI_FALLBACK_ENABLED=false
WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT=false
/run/secrets mounted read-only
uploads mounted explicitly
worker process
sync-daemon process
event-daemon process
restart policy
```

### Runbook quality bar

Every runbook should include:

```text
purpose
scope
affected services
preconditions
required permissions
required secrets without exposing values
commands
expected output
failure classification
rollback path
post-checks
operator handoff notes
```

Rollback instructions must state whether rollback is:

```text
code-only
config-only
image tag rollback
feature flag rollback
database forward-fix
restore from backup
not safely reversible
```

Incident classifications:

```text
config_error
secret_missing
provider_unavailable
upstream_timeout
schema_mismatch
migration_failed
permission_denied
rate_limited
queue_backlog
daemon_down
unsafe_to_continue
```

---

## 19. CI and workflow contract

Applies to:

```text
.github/workflows/**
```

Do not weaken CI to make a PR pass.

Before editing workflow, inspect:

```text
workflow being changed
test files invoked by workflow
package/requirements files used by workflow
path filters and changed-file guards
branch triggers
permissions block
concurrency block
artifact upload behavior
```

Hard rules:

```text
Do not remove tests without equivalent/stronger replacement.
Do not broaden/narrow path filters to hide critical changes.
Do not add write permissions without need.
Do not remove timeout-minutes without reason.
Do not remove set -Eeuo pipefail from shell gates.
Do not change pull_request triggers to push-only for quality gates.
Do not mark failing security/runtime tests as allowed failure without approval.
Align Node/Python versions with Dockerfile/package/runtime baselines.
```

WebCall PR guard rule:

```text
If a new WebCall/Codex/WebChat voice file is added, update the guard allowlist deliberately.
Do not bypass the guard by moving risky code into unguarded paths.
```

---

## 20. Test contract

Tests are production evidence, not decoration.

Select tests by impacted behavior:

```text
API route changed       -> route tests + service tests + auth/permission tests
service changed         -> service tests + calling API tests
model/migration changed -> migration tests + model users + API/service tests
WebChat changed         -> WebChat fast/event/security/provider tests
WebCall changed         -> voice/WebCall production tests + PR guard tests
Provider/Codex changed  -> provider runtime + Codex + WebChat provider tests
OpenClaw changed        -> bridge/sync/unresolved/worker/runtime-health tests
Speedaf changed         -> Speedaf action + background job + audit/idempotency tests
Storage/files changed   -> file visibility + MIME/size/storage readiness tests
```

Good tests assert production contracts:

```text
security gate holds
rate limit holds
idempotency holds
fallback is safe
audit/event row is written
queue job is deduped or retryable
response shape remains compatible
PII is redacted
feature flag defaults safe/off
demo route is not production route
```

Avoid:

```text
import-only smoke without behavior assertion
mocking away the exact boundary being tested
duplicating tests without new coverage
snapshot-only tests for critical API behavior
sleep-based flaky tests
```

If a test cannot run locally, report:

```text
exact command
exact failure/blocker
whether CI can run it
risk of not running it locally
next verification command
```

---

## 21. Security and architecture documentation contract

Applies to:

```text
docs/security/**
docs/architecture/**
docs/engineering/**
```

Security docs must include:

```text
threat model
assets/secrets involved
trust boundaries
allowed authority
forbidden authority
code paths
config/env vars
tests/validation
rollout/rollback
open risks
```

Architecture docs must include:

```text
system goal
non-goals
current code paths
data model
API contracts
state transitions
failure modes
observability
migration/deployment impact
acceptance criteria
```

Engineering execution packs must include:

```text
objective
exact files to change
exact functions/classes/routes
data model impact
before/after behavior
patch strategy
tests to run
acceptance criteria
rollback
```

No conceptual architecture without code anchors.

---

## 22. Business closed-loop standards

A feature is not closed-loop unless it has entrypoint, state, data persistence, UI/operator visibility, error handling, audit/observability, tests, and rollback/fallback.

### WebChat customer tracking inquiry

```text
Entry: public WebChat `/api/webchat/fast-reply` or stream
State: conversation, business state, idempotency row
Data: tracking fact result redacted before prompt/reply
Policy: origin/rate/idempotency/handoff
UI: operator can see resulting conversation/ticket if escalated
Failure: safe ack/handoff/provider fallback
Tests: WebChat fast, tracking fact, provider fallback, rate/idempotency
```

### WebChat handoff

```text
Entry: server handoff policy or AI handoff result
State: conversation + ticket + system handoff message
Data: ticket fields, tracking number if safe, recommended action
Policy: no direct customer send outside NexusDesk
UI: case workspace and WebChat operator view
Failure: enqueue/ticket creation failure mapped safely
Tests: handoff policy, ticket creation, timeline, operator queue
```

### WebCall visitor session

```text
Entry: `/api/webcall-ai/sessions`
State: session public id, visitor token, room/join token, events
Data: tracking fallback, handoff request, session end
Policy: visitor token and runtime feature flags
UI: WebCall/WebCall AI operator routes
Failure: degraded provider state, cleanup, end/reject safety
Tests: WebCall production, voice loop, voice static/header tests
```

### Operator outbound reply

```text
Entry: case workspace or WebChat operator reply
State: ticket outbound message or conversation message
Data: body, channel, status, provider status, retries
Policy: capability and outbound channel capability check
UI: visible success/failure/requeue state
Failure: queue retry/requeue/dead-letter behavior
Tests: outbound safety, message semantics, timeline
```

### Codex provider fast reply

```text
Entry: WebChat Fast provider router
State: provider routing rule, canary, kill switch, sidecar request
Data: strict Fast Lane JSON only
Policy: Codex reply-only, Nexus parser and safety gate
UI: provider runtime status and credential status
Failure: fallback providers or safe ack; no fail-open
Tests: provider runtime, Codex sidecar, WebChat Codex provider
```

### OpenClaw transcript sync

```text
Entry: sync daemon, event daemon, admin consume-once, reconciliation
State: conversation link, transcript messages, cursor, heartbeat
Data: OpenClaw messages and attachment references
Policy: MCP primary route, no direct bypass
UI: runtime health, unresolved events, ticket transcript/evidence
Failure: unresolved event persistence, replay/drop, stale reconciliation
Tests: OpenClaw bridge, unresolved idempotency, daemon readiness
```

### Speedaf work-order/action

```text
Entry: explicit API or WebChat delivery follow-up enqueue
State: background job, audit/event, ticket linkage
Data: redacted tracking/caller details where required
Policy: feature flag, capability, idempotency, audit
UI: visible ticket/job status where applicable
Failure: retry/dead-letter/compensation note
Tests: Speedaf action, background job, audit/idempotency
```

### Attachment upload/download/evidence

```text
Entry: file API, OpenClaw attachment reference, remote media fetch
State: TicketAttachment or OpenClawAttachmentReference
Data: storage_key, metadata, MIME, size, visibility
Policy: ticket visibility, MIME/size/host/timeouts, no raw path exposure
UI: ticket detail/evidence blocks
Failure: metadata-only fallback or safe error
Tests: file visibility/security/storage readiness
```

---

## 23. Command packs

### Backend baseline

```bash
set -Eeuo pipefail
python -m pip install --upgrade pip
pip install -r backend/requirements.txt
PYTHONPATH=backend python -m compileall backend/app backend/scripts
PYTHONPATH=backend pytest -q backend/tests
```

### Frontend baseline

```bash
set -Eeuo pipefail
cd webapp
npm ci
npm run lint
npm run typecheck
npm test
npm run build
npm run size-report
npm run e2e
```

### Codex runtime

```bash
set -Eeuo pipefail
cd tools/nexus-codex-runtime
npm ci
npm run build
npm test
```

### Alembic migration check

```bash
set -Eeuo pipefail
cd backend
PYTHONPATH=. alembic heads
PYTHONPATH=. alembic history --verbose
PYTHONPATH=. alembic upgrade head
```

### Server deployment validation template

Do not run against a live host without explicit approval.

```bash
set -Eeuo pipefail
docker compose -f deploy/docker-compose.server.yml build
docker compose -f deploy/docker-compose.server.yml run --rm app alembic upgrade head
docker compose -f deploy/docker-compose.server.yml up -d
curl -fsS http://127.0.0.1:18081/healthz
curl -fsS http://127.0.0.1:18081/readyz
```

---

## 24. Generated files and lockfile policy

Do not commit:

```text
frontend_dist/
__pycache__/
*.pyc
.pytest_cache/
playwright-report/
test-results/
coverage/
dist/ build artifacts unless package-owned and intended
runtime logs
local sqlite DB files
uploads
data/
.env files with real values
```

`package-lock.json` may change only when:

```text
package.json dependency/script change requires it;
npm install legitimately updates lock for the package being changed;
PR explains why lockfile changed.
```

Do not modify lockfiles casually.

---

## 25. Local validation fallback policy

If local validation cannot run:

```text
1. report exact command attempted
2. report exact error/blocker
3. run all lower-cost checks that do not require the missing dependency
4. list CI jobs that must validate it
5. do not claim full validation
```

Example report:

```text
Validation skipped: npm run e2e
Reason: Playwright browser dependencies unavailable in local container
Completed instead: npm run lint, npm run typecheck, npm test, npm run build
Required CI: webapp-build Playwright e2e smoke
Risk: route-level browser behavior not locally verified
```

---

## 26. Branch, PR, and multi-agent workflow

Do not commit directly to `main`. Use purpose-specific branches:

```text
docs/<topic>
fix/<area>-<issue>
feat/<area>-<capability>
hardening/<area>-<risk>
```

Before pushing updates to an existing PR:

```text
compare branch with main
avoid force-push unless explicitly approved
do not rewrite other agent commits without approval
update PR body with delta
if conflict appears, report conflicting files and resolution plan
```

PR body must include:

```markdown
## Summary
## Evidence
## Validation
## Risk
## Rollback
```

PR checklist:

```markdown
- [ ] I inspected this root AGENTS.md.
- [ ] I classified the change type.
- [ ] I listed touched routes/services/models/tests.
- [ ] I did not modify generated assets unless explicitly intended.
- [ ] I did not expose secrets or PII.
- [ ] I preserved auth, permission, idempotency, rate limit, audit, and rollback controls.
- [ ] I updated frontend API/types when backend response shape changed.
- [ ] I added/updated regression tests when fixing a bug.
- [ ] I ran required targeted checks or documented exact blockers.
- [ ] I documented rollback.
```

---

## 27. Human approval gates

Stop and ask for explicit approval before:

```text
pushing directly to main
merging PRs
running production deployment commands
modifying live server files
changing real DNS/Nginx/TLS
changing secrets or credential custody
enabling real customer outbound
enabling Speedaf write actions
changing live database schema
deleting data or uploaded files
increasing AI autonomy beyond reply-only or safe-ack behavior
force-pushing over another agent's branch
weakening CI/security gates
```

---

## 28. Task templates

### Audit task template

```markdown
## Scope
## Facts verified
## Files inspected
## Findings
### FACT
### INFERENCE
### UNKNOWN
### RISK
## Required fixes
## Validation commands
## Next action
```

### Implementation task template

```markdown
## Objective
## Impacted contracts
## Files to inspect first
## Patch plan
## Tests to run
## Stop conditions
## Rollback plan
## Acceptance criteria
```

### Production deploy task template

```markdown
## Target environment
## Current version
## Target version
## Backup plan
## Migration plan
## Rollout commands
## Health checks
## Log checks
## Smoke tests
## Rollback trigger
## Rollback commands
## Evidence artifacts
```

### Cross-agent execution pack template

```markdown
## Objective
## Current verified facts
## Exact files to change
## Exact functions/classes/routes
## Data model impact
## Before behavior
## After behavior
## Patch strategy
## Tests to run
## Acceptance criteria
## Rollback
## Do-not-touch list
```

---

## 29. Final response format for agents

End every implementation or audit response with:

```markdown
## Result
- Outcome in one paragraph.

## Files changed / inspected
- `path`: reason.

## Validation
- `command`: passed/failed/skipped with reason.

## Risk
- Residual risks and assumptions.

## Rollback
- Exact revert/config rollback path.

## Next action
- One concrete next action.
```

---

## 30. Engineering posture

Default to production-grade execution, not prototype patches. Every change should improve at least one of:

```text
correctness
security
observability
operator usability
business workflow closure
rollbackability
testability
runtime stability
maintainability
```

Do not optimize for cleverness. Optimize for controlled execution, evidence, and safe rollout.
