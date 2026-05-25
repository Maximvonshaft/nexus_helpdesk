# AGENTS.md — NexusDesk / Nexus Helpdesk Agent Execution Contract v2

This file is the root execution contract for AI coding agents working in `Maximvonshaft/nexus_helpdesk`. It applies to the whole repository unless a deeper `AGENTS.md` adds stricter instructions for a subdirectory.

This document is intentionally operational. It is not a style guide. It tells agents what to inspect, what not to touch, what tests to run, what evidence to report, and where human approval is mandatory.

## 1. Binding hierarchy

Follow instructions in this order:

```text
1. system / platform policy
2. user task
3. nearest subdirectory AGENTS.md
4. this root AGENTS.md
5. existing repository code, tests, docs, and CI
```

A subdirectory `AGENTS.md` may add detail, but it must not weaken these root safety gates.

## 2. Project mission

NexusDesk / Nexus Helpdesk is a case-centric customer operations runtime for logistics support and exception handling. It combines customer conversations, tickets, manual operator workflows, AI-assisted intake/reply, attachments, outbound replies, audit history, WebChat/WebCall, OpenClaw routing, Speedaf-related operational actions, and provider-runtime integrations.

The production objective is controlled customer-support automation. AI providers, OpenClaw, Codex, WebChat, WebCall, and Speedaf adapters must remain behind NexusDesk policy gates, audit trails, permissions, observability, feature flags, and rollback controls.

## 3. Current source-of-truth topology

```text
backend/app/main.py                         FastAPI app assembly, middleware, security headers, router registration, health/readiness/metrics, SPA fallback
backend/app/api/                            HTTP route surface
backend/app/services/                       business logic, provider runtime, storage, permissions, jobs, OpenClaw, WebChat, WebCall, Speedaf
backend/app/models.py                       SQLAlchemy domain model and table definitions
backend/app/settings.py                     runtime configuration, production guards, env validation
backend/alembic/                            Alembic migration environment and revisions
backend/scripts/run_worker.py               generic durable job / outbound worker
backend/scripts/run_openclaw_sync_daemon.py OpenClaw transcript reconciliation daemon
backend/scripts/run_openclaw_event_daemon.py OpenClaw event daemon
backend/tests/                              backend regression, contract, security, runtime, WebChat/WebCall tests

webapp/                                     modern React / TypeScript / Vite operator console
webapp/src/router.tsx                       frontend route tree
webapp/src/lib/api.ts                       frontend API client and backend contract map
frontend/                                   legacy static fallback only
frontend_dist/                              generated SPA build output; never edit or commit as source

tools/nexus-codex-runtime/                  Node/TypeScript Codex app-server runtime sidecar
deploy/                                     deployment templates, proxy scripts, compose, nginx/systemd samples
scripts/deploy/                             deployment preflight, migrations, backup, restore helpers
.github/workflows/                          CI definitions and PR guards
docs/architecture/                          architecture records
docs/ops/                                   operational runbooks
docs/security/                              security boundaries and risk records
```

## 4. Minimum-granularity workflow for every task

Do not start by patching. Start by building an evidence map.

```text
Step 1 — Classify the change
  docs-only | backend API | backend service | DB migration | webapp UI | WebChat | WebCall | OpenClaw | Codex provider runtime | Speedaf action | storage/attachment | deployment | CI

Step 2 — Identify impacted contracts
  routes | services | models/tables | migrations | frontend API client | frontend routes | workers/daemons | env vars | tests | deployment files | docs/runbooks

Step 3 — Inspect exact files before editing
  Never rely on filenames alone. Read the route, service, model, settings, migration, tests, and CI gates for the touched area.

Step 4 — Patch narrowly
  Preserve behavior, data compatibility, permissions, idempotency, rate limits, audit logs, feature flags, and rollback.

Step 5 — Validate by change type
  Run the smallest relevant tests first, then broaden when the area is public-facing, security-sensitive, queue-related, provider-related, or migration-related.

Step 6 — Report evidence
  Include paths, functions/routes/classes/tables, commands, outputs, risk, rollback, and residual unknowns.
```

## 5. Change-type execution matrix

| Change type | Required inspection | Required validation | Hard stop conditions |
|---|---|---|---|
| Docs-only | Existing docs, README, relevant code path if making technical claims | Markdown review; no secrets; no generated/runtime artifacts | Any unverified technical claim |
| Backend route | `backend/app/main.py`, target `backend/app/api/*.py`, auth deps, service, model/table, tests | `PYTHONPATH=backend python -m compileall backend/app backend/scripts`; targeted pytest | Missing permission check, missing idempotency on writes, route not registered, public API without rate/origin policy |
| Backend service | Calling API route, target service, models, settings, job/worker path, tests | compileall; targeted pytest; broader backend tests if security/queue/provider | Service commits hidden inside nested transaction without reason; bypasses audit/policy gate |
| DB migration | `backend/app/models.py`, `backend/alembic/env.py`, current heads, related service/tests | `cd backend && PYTHONPATH=. alembic heads && PYTHONPATH=. alembic history --verbose && PYTHONPATH=. alembic upgrade head` | Editing applied migration without explicit pre-release repair context; non-reversible migration without rollback note |
| Webapp UI | `webapp/src/router.tsx`, `webapp/src/lib/api.ts`, relevant route/component, tests | `cd webapp && npm ci && npm run typecheck && npm test && npm run build` plus `npm run e2e` for behavior changes | UI button without real API binding or explicit disabled reason; auth token moved from sessionStorage to localStorage |
| WebChat fast path | `backend/app/api/webchat_fast.py`, `webchat_fast_*` services, provider router, rate/idempotency tests, frontend widget if touched | WebChat tests from backend CI; stream tests if `/fast-reply/stream` touched | Origin/rate/idempotency bypass; unsafe AI fallback; customer-visible reply without policy gate |
| WebCall / voice | `backend/app/api/webcall_ai.py`, `admin_webcall_ai.py`, `webcall_ai_production/**`, voice config, webapp WebCall routes/components, PR WebCall guard | WebCall backend tests; webapp typecheck/test/build; Playwright where UI changed | Microphone/camera permission outside intended path; demo flow exposed as production; no fallback when provider unavailable |
| Provider Runtime / Codex | `backend/app/api/admin_provider_runtime.py`, `admin_provider_credentials.py`, `backend/app/services/provider_runtime/**`, `backend/app/services/ai_runtime/**`, `tools/nexus-codex-runtime/**`, deploy Codex proxies, provider tests | Codex runtime `npm test`; provider-runtime pytest; WebChat provider tests | Codex gets direct ticket/file/shell/customer-send authority; secrets exposed; strict reply parser weakened; fail-open behavior |
| OpenClaw | OpenClaw bridge/service files, `OpenClaw*` models, sync/event daemons, runtime health admin APIs, unresolved-event tests | OpenClaw runtime/observability pytest groups; daemon readiness tests | Direct send outside NexusDesk audit/policy; cursor/idempotency broken; CLI fallback enabled in production without runbook |
| Speedaf action | `speedaf_actions`, `speedaf_cancel`, Speedaf services, background job enqueue/worker, audit, feature flags, tests | Speedaf action tests plus queue/job tests | Operational write enabled without capability, idempotency, audit, feature flag, rollback/compensation |
| Storage / attachment | `files` API, storage services, `TicketAttachment`, `OpenClawAttachmentReference`, settings, upload tests | file/security tests; storage readiness checks | Raw file path exposed; MIME/size/host timeout guard weakened; ticket visibility bypass |
| Deployment | `Dockerfile`, `deploy/**`, `scripts/deploy/**`, settings production guards, README deployment notes | compose build where feasible; migration command; healthz/readyz; do not run live deploy without approval | Overwriting live `.env.prod`, `data/`, uploads, token files; destructive server cleanup |
| CI/workflows | Target workflow plus test files it invokes | YAML sanity; reason through triggers/paths; avoid weakening gates | Removing security/test gate to make PR pass without replacement |

## 6. Non-negotiable global rules

1. Inspect before modifying. Read exact routes, services, models, migrations, tests, and deployment files.
2. Base conclusions on repository files, commands, tests, logs, CI, or reproducible output.
3. Prefer surgical patches. Preserve public contracts unless an approved task explicitly changes them.
4. Preserve backward compatibility for API contracts, database data, migrations, UI workflows, and deployment defaults.
5. Fail closed for AI, provider runtime, credentials, outbound messaging, Speedaf actions, WebCall, WebChat, authentication, storage, and metrics.
6. Never commit secrets, real `.env` files, tokens, passwords, cookies, session dumps, private gateway URLs, private IPs, Tailscale addresses, customer PII, or local runtime artifacts.
7. Never run destructive production commands from an agent session: no live `git reset --hard`, no `rm -rf data`, no unreviewed database drop/downgrade, no direct traffic switch, no unapproved deploy.
8. Do not send real customer outbound messages, perform Speedaf writes, modify provider credentials, trigger refunds/claims/address changes, or operate OpenClaw accounts during tests unless the user explicitly authorizes that exact action.
9. Any mock, placeholder, fake button, or unconnected UI must be labeled as such in code and PR. Production-facing UI must call real APIs.
10. Every PR must include validation evidence and rollback.

## 7. Business invariants that must not regress

### WebChat

- Public WebChat requests must preserve allowed-origin checks, rate limits, idempotency, request hash protection, no-store headers, and safe fallback.
- `/api/webchat/fast-reply` and `/api/webchat/fast-reply/stream` must not bypass server-owned context, tracking fact redaction, handoff policy, or NexusDesk ticket/audit creation.
- AI/provider failure must degrade to safe ack, server policy, or handoff; never fail open with unsafe customer guidance.

### WebCall / voice

- Microphone permission must stay limited to intended voice paths.
- Demo/sandbox routes must not become production customer entrypoints without explicit feature flag and tests.
- Session create/join/end/handoff/tracking fallback/events must preserve visitor-token checks and idempotency where present.

### Provider Runtime / Codex

- Codex is a controlled provider behind NexusDesk, not an autonomous operator.
- Codex is reply-only unless an approved design changes it.
- Codex must not directly execute shell commands, write files, scrape cookies, scrape browser sessions, run model-native tools, create/modify tickets, send customer outbound messages, perform refunds, change addresses, submit claims, or execute Speedaf actions.
- Strict Fast Lane JSON parsing, redaction, private URL restrictions, timeout, queue, and fallback behavior must remain intact.

### OpenClaw

- OpenClaw MCP is the preferred integration route.
- CLI fallback must stay disabled in production unless a documented recovery runbook explicitly permits it.
- OpenClaw transcript sync, event cursors, unresolved event idempotency, route/account mapping, and runtime-health visibility must not regress.
- Do not bypass NexusDesk by sending directly through OpenClaw without audit, policy, and operator visibility.

### Speedaf operational actions

- Operational writes must be capability-gated, audited, idempotent, feature-flagged, and rollback/compensation-aware.
- Read/tracking facts must remain redacted before entering prompts or customer-visible payloads.

### Storage and attachments

- Customer-visible download must pass ticket visibility checks.
- Remote media fetch must enforce allowed hosts, MIME limits, byte limits, timeouts, and redaction policy.
- Continue moving toward `storage_key` as canonical persistence; do not expand `file_path`/`file_url` compatibility debt.

## 8. Required command packs

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

## 9. Evidence standard

Every implementation or audit response must include:

```text
file path
function/class/route name
model/table/migration name
environment variable
command run
exit status
test output or explicit skipped reason
log excerpt when relevant
risk and rollback
```

Do not write `done`, `works`, or `production-ready` without validation evidence. If a command cannot run, state the exact blocker and the next verification command.

## 10. PR discipline

Do not commit directly to `main`. Use a purpose-specific branch:

```text
docs/<topic>
fix/<area>-<issue>
feat/<area>-<capability>
hardening/<area>-<risk>
```

PR descriptions must include:

```markdown
## Summary
## Evidence
## Validation
## Risk
## Rollback
```

## 11. Human approval gates

Stop and ask for explicit approval before:

- pushing directly to `main`;
- merging PRs;
- running production deployment commands;
- modifying live server files;
- changing real DNS/Nginx/TLS;
- changing secrets or credential custody;
- enabling real customer outbound;
- enabling Speedaf write actions;
- changing live database schema;
- deleting data or uploaded files;
- increasing AI autonomy beyond reply-only or safe-ack behavior.

## 12. Subdirectory execution contracts

Additional minimum-granularity rules exist or should exist in:

```text
backend/app/api/AGENTS.md
backend/app/services/AGENTS.md
backend/alembic/AGENTS.md
webapp/AGENTS.md
tools/nexus-codex-runtime/AGENTS.md
deploy/AGENTS.md
.github/workflows/AGENTS.md
```

When working inside those paths, read the nearest `AGENTS.md` first.

## 13. Final output format

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

## 14. Engineering posture

Default to production-grade execution, not prototype patches. Every change should improve at least one of correctness, security, observability, operator usability, workflow closure, rollbackability, testability, runtime stability, or maintainability.

Do not optimize for cleverness. Optimize for controlled execution, evidence, and safe rollout.
