# AGENTS.md — NexusDesk / Nexus Helpdesk

This file defines how AI coding agents must work in this repository. It applies to the whole repository unless a deeper `AGENTS.md` overrides it for a subdirectory.

Generated for `Maximvonshaft/nexus_helpdesk` from the current `main` line of work on 2026-05-25. Always re-check the latest code before changing behavior; this file is an execution contract, not a substitute for code inspection.

## 1. Project mission

NexusDesk / Nexus Helpdesk is a case-centric customer operations runtime for logistics support and exception handling. It is not a generic helpdesk demo. The system unifies customer conversations, tickets, manual operator workflows, AI-assisted intake/reply, attachments, outbound replies, audit history, WebChat/WebCall, OpenClaw routing, Speedaf-related operational actions, and provider-runtime integrations.

The production objective is controlled customer-support automation. AI providers, OpenClaw, Codex, WebChat, WebCall, and Speedaf adapters must remain behind NexusDesk policy gates, audit trails, permissions, and rollback controls.

## 2. Repository map

Use these paths as the current source-of-truth layout:

```text
backend/app/api/                         FastAPI route surface
backend/app/services/                    business logic, provider runtime, storage, permissions, jobs
backend/app/models.py                    SQLAlchemy domain model
backend/alembic/                         schema migrations
backend/scripts/run_worker.py            generic durable job / outbound worker
backend/scripts/run_openclaw_sync_daemon.py
backend/scripts/run_openclaw_event_daemon.py
backend/tests/                           backend regression and contract tests

webapp/                                  modern React / TypeScript / Vite operator console
frontend/                                legacy static fallback only
frontend_dist/                           generated SPA build output; do not edit or commit

tools/nexus-codex-runtime/               Node/TypeScript Codex app-server runtime sidecar
deploy/                                  deployment templates, examples, proxies, systemd/nginx samples
scripts/deploy/                          deployment, migration, backup, restore helpers
.github/workflows/                       CI definitions
docs/architecture/                       architecture records
docs/ops/                                runbooks
docs/security/                           security boundaries and risk records
```

`webapp/` is the only source of truth for the modern operator UI. `frontend_dist/` is generated output. Do not patch built assets as a shortcut.

## 3. Non-negotiable operating rules

1. Inspect before modifying. Do not infer behavior from names alone. Read the route, service, model, migration, test, and deployment file involved.
2. Base every technical conclusion on actual repository files, commands, tests, logs, or reproducible output.
3. Prefer small, surgical patches over broad rewrites. Preserve existing behavior unless the task explicitly changes it.
4. Keep backward compatibility for API contracts, database migrations, stored data, and UI workflows unless a breaking change is explicitly approved.
5. Fail closed for AI, provider runtime, credentials, outbound messaging, Speedaf actions, WebCall, WebChat, authentication, and storage.
6. Never commit secrets, real `.env` files, tokens, passwords, cookies, session dumps, private gateway URLs, private IPs, Tailscale addresses, customer PII, or local runtime artifacts.
7. Never run destructive production commands from an agent session: no live `git reset --hard`, no `rm -rf data`, no unreviewed database drop/downgrade, no direct traffic switch, no unapproved deploy.
8. Do not send real customer outbound messages, perform Speedaf operational writes, modify provider credentials, trigger refunds/claims/address changes, or operate OpenClaw accounts during tests unless the user explicitly authorizes that exact action.
9. Any mock, placeholder, fake button, or unconnected UI must be labeled as such in the code and in the PR. Production-facing UI must call real API surfaces.
10. Every PR must include validation evidence and a rollback plan.

## 4. Branch, commit, and PR discipline

Do not commit directly to `main`. Use a purpose-specific branch:

```text
docs/<topic>
fix/<area>-<issue>
feat/<area>-<capability>
hardening/<area>-<risk>
```

Commit messages should be specific and production-readable, for example:

```text
docs: add repository agent execution contract
fix(webchat): preserve rate-limit isolation on event writes
hardening(codex): fail closed on invalid strict reply payload
```

PR descriptions must include:

```markdown
## Summary
- What changed and why.

## Evidence
- Exact files/functions/routes/models/migrations touched.
- Actual command output or CI links.

## Validation
- Commands run.
- Tests passed/failed/skipped with reason.

## Risk
- Runtime risk, migration risk, security risk, customer-impact risk.

## Rollback
- Exact revert path, feature flag, config rollback, migration rollback constraints.
```

## 5. Backend rules

The backend is a FastAPI application using SQLAlchemy, Alembic, PostgreSQL for production, durable jobs, structured logging, request IDs, readiness probes, and provider/runtime integrations.

Backend agents must follow these rules:

- Route changes belong under `backend/app/api/**`.
- Business behavior belongs under `backend/app/services/**`.
- Persistent domain changes belong in `backend/app/models.py` plus an explicit Alembic revision under `backend/alembic/versions/**`.
- Do not use runtime `create_all/drop_all` semantics for production schema changes.
- Do not add service-level scattered commits unless the surrounding code already requires it and the PR explains why. Prefer explicit use-case transaction boundaries and existing managed-session helpers.
- Preserve tenant/account/customer/ticket visibility checks.
- Preserve idempotency for write endpoints and queues.
- Preserve rate limits for WebChat, integration clients, admin actions, and public entrypoints.
- Preserve request-id propagation and structured observability.
- Keep `/healthz`, `/readyz`, and `/metrics` behavior safe. `/metrics` must remain token-gated when enabled.
- In production, PostgreSQL is required. SQLite is only acceptable for local development and tests.

Backend baseline validation:

```bash
set -Eeuo pipefail
python -m pip install --upgrade pip
pip install -r backend/requirements.txt
PYTHONPATH=backend python -m compileall backend/app backend/scripts
PYTHONPATH=backend pytest -q backend/tests
```

For focused changes, run the narrow relevant tests first, then run broader tests if the touched area is security-sensitive, queue-related, provider-related, migration-related, or customer-facing.

## 6. Frontend rules

The modern frontend is `webapp/`, using React 18.3.1, TypeScript, Vite, TanStack Router, TanStack Query, Tailwind CSS v4, Radix UI primitives, and Playwright.

Frontend agents must follow these rules:

- Edit `webapp/src/**`, not `frontend_dist/**`.
- Do not treat `frontend/` as the main UI unless the task is specifically about legacy fallback.
- Keep authenticated operator workflows aligned with real backend API contracts.
- Use typed API boundaries. Do not add stringly-typed ad hoc fetch logic when an existing API client or query layer should be extended.
- Preserve session-token safety. Do not move auth material back into `localStorage`.
- No dead controls. Buttons must either call real logic, be hidden, or be explicitly disabled with a clear reason.
- Keep information architecture oriented around operator work: queue, case workspace, customer context, transcript, WebCall/WebChat status, evidence, bulletins, channel accounts, runtime health, sign-off.
- Preserve accessibility basics: semantic labels, keyboard operation for controls, clear loading/error/empty states.

Frontend validation:

```bash
set -Eeuo pipefail
cd webapp
npm ci
npm run typecheck
npm test
npm run build
npm run size-report
npm run e2e
```

If local Playwright browser dependencies are not available, state that explicitly and provide the command output for all earlier completed steps.

## 7. Provider Runtime and Codex rules

Codex must be treated as a controlled provider behind NexusDesk, not as an autonomous operator of NexusDesk.

Current provider-runtime direction:

```text
WebChat customer
-> /api/webchat/fast-reply
-> Nexus provider_router
-> codex_app_server provider
-> private sidecar /reply
-> private upstream adapter /reply
-> private Codex app-server reply endpoint
-> strict Fast Lane JSON
-> Nexus strict parser / safety gate
-> customer reply or fallback
```

Hard rules:

- Codex is reply-only unless a future approved design explicitly changes this.
- Codex must not directly execute shell commands, write files, scrape cookies, scrape browser sessions, run model-native tools, create/modify tickets, send direct customer outbound messages, perform refunds, change addresses, submit claims, or execute Speedaf actions.
- Keep provider capability surfaces explicit and safe.
- Keep strict Fast Lane JSON parsing and fail-closed behavior.
- Do not echo raw upstream payloads or secrets in admin status endpoints, logs, API responses, or PR comments.
- Keep public URL restrictions and private-upstream guardrails intact.
- Provider credential mutation surfaces must remain disabled/unmounted unless the task explicitly approves custody workflow changes and adds tests.
- Any token handling must prefer file-based secrets or `/run/secrets` style custody over environment-variable echoing.

Codex runtime validation:

```bash
set -Eeuo pipefail
cd tools/nexus-codex-runtime
npm ci
npm test
```

Provider-runtime backend validation should include relevant tests such as provider status, adapter boundary, strict reply parsing, WebChat Codex provider behavior, and credential custody tests when touched.

## 8. OpenClaw rules

OpenClaw MCP is the preferred integration route for conversation history, transcript reads, attachment metadata, and same-route replies. CLI fallback must stay disabled in production unless an explicit recovery runbook says otherwise.

Agents working in OpenClaw areas must preserve:

- channel-account routing;
- market/account strategy;
- conversation ownership state;
- transcript sync and reconciliation;
- event cursor handling;
- sync daemon and event daemon health;
- attachment reference capture and safe persistence;
- unresolved event idempotency;
- runtime-health visibility.

Do not bypass NexusDesk by letting OpenClaw send directly to customers without the NexusDesk policy gate, audit path, and operator visibility.

## 9. WebChat and WebCall rules

WebChat and WebCall are production-facing customer entrypoints. Changes here require extra caution.

Preserve these controls:

- CORS origin rules;
- WebChat allowed-origin checks;
- WebChat rate limits;
- no legacy token transport in production;
- request-id propagation;
- no-store cache headers on `/api/**`;
- content security policy;
- microphone permissions only on intended voice paths;
- safe fallback behavior when AI/provider runtime fails;
- ticket/customer/tracking binding correctness;
- auditability of final customer-visible actions.

For WebCall/WebChat UI, do not add demo-only flows to production routes without feature flags and clear labels.

## 10. Speedaf and operational action rules

Speedaf-related actions must remain controlled, audited, and feature-flagged.

Do not enable or expand operational writes such as cancel, address update, work-order action, callback, claim, compensation, refund, dispatch, or external provider mutation without:

- explicit capability check;
- tenant/operator authorization;
- idempotency;
- audit log;
- rollback or compensation path;
- test coverage;
- clear feature flag defaulting safe/off when appropriate.

## 11. Storage and attachments

The repository supports local and S3-compatible storage behind one abstraction.

Rules:

- Production-compatible strict mode should prefer S3-compatible object storage when available.
- Local storage may be used for controlled pilot or local development only when deployment policy allows it.
- Do not expose raw local file paths to customers.
- Preserve upload MIME/extension/size checks.
- Preserve ticket visibility checks before attachment access.
- Continue moving toward `storage_key` as canonical persistence when modifying attachment code.
- Any remote media fetch must enforce allowed hosts, MIME limits, byte limits, timeout, and redaction policy.

## 12. Database and Alembic rules

Any schema change requires an Alembic migration. The migration must be explicit, immutable, and safe to run once in a real environment.

Before merging migration changes:

```bash
set -Eeuo pipefail
cd backend
PYTHONPATH=. alembic heads
PYTHONPATH=. alembic history --verbose
PYTHONPATH=. alembic upgrade head
```

If a migration is not reversible, state that in the PR and provide the operational rollback strategy. Do not silently edit old applied migrations unless the task is explicitly about pre-release migration repair and the database state is known.

## 13. Deployment rules

Deployment templates are under `deploy/`. Live server state must remain separate from Git-tracked source.

Do not commit or overwrite:

```text
deploy/.env.prod
data/
uploaded attachments
local storage roots
server-only compose overrides
private reverse-proxy files
private token files
```

For server-style deployment validation:

```bash
set -Eeuo pipefail
docker compose -f deploy/docker-compose.server.yml build
docker compose -f deploy/docker-compose.server.yml run --rm app alembic upgrade head
docker compose -f deploy/docker-compose.server.yml up -d
curl -fsS http://127.0.0.1:18081/healthz
curl -fsS http://127.0.0.1:18081/readyz
```

Do not run these against a live production host unless explicitly authorized for that host and maintenance window.

## 14. CI expectations

The repository has CI for backend, backend full regression, and webapp build/e2e. Agents should align local validation with CI.

Expected validation by change type:

```text
Docs-only:
  - markdown review
  - no generated/runtime artifacts
  - no secrets

Backend code:
  - PYTHONPATH=backend python -m compileall backend/app backend/scripts
  - targeted pytest for touched area
  - broader backend/tests if security/queue/provider/customer-facing

Frontend code:
  - cd webapp && npm ci
  - npm run typecheck
  - npm test
  - npm run build
  - npm run size-report
  - npm run e2e when UI behavior changed

Codex runtime:
  - cd tools/nexus-codex-runtime && npm ci && npm test
  - provider-runtime backend tests when Nexus boundary changed

Migration:
  - Alembic heads/history/upgrade
  - migration-specific tests
  - rollback note

Docker/deploy:
  - docker build or compose build where feasible
  - migration command dry-run or staged run
  - healthz/readyz verification
```

## 15. Evidence standard

When reporting work, include exact evidence:

```text
file path
function/class/route name
model/table/migration name
environment variable
command run
exit status
test output
log excerpt
risk and rollback
```

Do not write "done", "works", or "production-ready" without validation evidence. If a command cannot run in the current environment, state the exact blocker and provide the next verification command.

## 16. Security standard

Security-sensitive areas include:

```text
auth
JWT/session handling
admin APIs
provider credentials
OpenClaw/Codex token custody
Speedaf operational actions
attachment fetch/download
WebChat/WebCall public entrypoints
CORS/CSP/permissions policy
rate limits
storage backends
deployment env files
logging/redaction
```

For these areas, require tests and fail-closed behavior. Do not downgrade a production guard to make a test pass.

## 17. Agent output format

Use this format at the end of every implementation or audit response:

```markdown
## Result
- One-paragraph outcome.

## Files changed / inspected
- `path`: purpose.

## Validation
- `command`: passed/failed/skipped with reason.

## Risk
- Residual risks and assumptions.

## Rollback
- Exact revert/config rollback path.

## Next action
- One concrete next action, not a generic suggestion.
```

## 18. Human approval gates

Stop and ask for explicit approval before:

- pushing directly to `main`;
- merging PRs;
- running production deployment commands;
- modifying live server files;
- changing real DNS/Nginx/TLS;
- changing secrets or credential custody;
- enabling real customer outbound;
- enabling Speedaf write actions;
- changing database schema on a live database;
- deleting data or uploaded files;
- increasing AI autonomy beyond reply-only or safe-ack behavior.

## 19. Preferred engineering posture

Default to production-grade implementation, not prototype patches. The target is a stable logistics customer-operations control layer that can safely grow from customer support into operational control. Every change should improve at least one of:

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
