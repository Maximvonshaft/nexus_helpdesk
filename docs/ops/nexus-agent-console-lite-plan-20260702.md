# Nexus Lightweight Agent Console Plan

Date: 2026-07-02

## Objective

Make the Nexus backend feel as lightweight as the current Speedy Console while keeping the real Nexus capabilities that matter for support staff. The first customer scenario is WebChat Demo: a customer writes in, the runtime answers when safe, a support agent can take over, verify facts, reply, escalate, and close the loop.

This plan is grounded in the current deployed candidate facts:

- `www.leakle.com` is serving the Nexus candidate after the OpenClaw runtime removal from the app image.
- 178 app and worker now both run `nexusdesk/helpdesk:manual-agent-console-demo-hygiene-20260701T231728Z`; the candidate WhatsApp sidecar was preserved.
- WebChat Demo text flow is live through `provider_runtime -> private_ai_runtime`.
- Visible canned WebChat fallback bubbles must stay removed. The 2026-07-02 live check found the deployed 178 HTML still had an old static welcome bubble; the demo static page is now part of the deployment patch set and smoke guardrails, and 178 has been redeployed with the fix.
- WebChat voice remains disabled/mock-backed on the runtime-config endpoint. The public demo must not expose the retired live edge-card voice entry until voice is separately wired.
- The candidate uses native WhatsApp sidecar mode, but the active customer text path remains WebChat.
- The 178 server residual duplicate candidate containers/images were cleaned after verified replacement paths passed.

## Speedy Practices To Absorb

Speedy Console is useful because it is narrow, not because it has more screens:

- Login-gated workbench first, customer data hidden until authenticated.
- Small navigation: takeover, knowledge/reply config, config overview, QA regression, service status.
- Operational copy uses current state and next task, not roadmap language.
- Configuration views show redacted values and explicit readiness.
- QA/regression is a first-class operator workflow, not a separate engineering artifact.
- Service health is visible without forcing agents into infrastructure details.

Speedy DSP also contributes patterns, but it should stay manager/Ops-only:

- Direct inputs, immediate result panels, and scenario presets.
- Transparent assumptions and formula outputs.
- Heatmap/map views for planning, not for daily support handling.

Do not copy Speedy dead surfaces blindly. The observed support-agent API on `127.0.0.1:18789` was not listening, so Nexus should absorb the product patterns and only reuse code when it is backed by live API contracts.

## Target Information Architecture

Default support-agent mode:

- Today: SLA, waiting conversations, assigned work, urgent exceptions.
- Customer Conversations: WebChat now; WhatsApp, WebCall, Email only when backed by real adapters.
- Case Workspace: ticket, customer, shipment evidence, notes, safe actions.
- Knowledge Hints: published bulletins and answer evidence visible inside the workflow.
- Channel Status: read-only readiness for the agent.

Manager/Ops mode:

- Knowledge Studio: publish, retrieval test, golden tests, conflict scan.
- Reply/AI Rules: persona, guardrails, memory configuration, publish evidence.
- Channel Admin: WhatsApp native binding, Email accounts, provider credentials.
- QA Training: samples, appeals, knowledge gaps.
- Runtime: health, dead/requeue, release metadata, rollback evidence.
- Users/Security: capabilities, audit, high-risk confirmations.

## Implementation Phases

## Current Landed State

This branch has already landed the first two low-risk cuts:

- P1 agent surface: AppShell navigation is grouped by support-agent workflow first, and WebChat opens on a compact `agent-console-strip` instead of roadmap/model cards.
- P1 smoke: `webapp/e2e/smoke.spec.ts` covers opening the lightweight WebChat agent console with mocked real API shapes.
- P2 status dictionary backend: `/api/support-intelligence/status-dictionary`, `/draft`, and `/publish` now use Nexus `AIConfigResource` and `AIConfigVersion` instead of the retired status-dictionary bridge.
- P2 config summary: `build_support_intelligence_config` reads status dictionary source/version/counts from Nexus DB and reports `source=nexus_ai_config_resources`.
- P2 guardrail: tests assert `_bridge_status_dictionary` and `legacy_status_dictionary_runtime_bridge_retired` are not present in the support-intelligence API.
- Demo hygiene: `backend/app/static/webchat/demo/index.html` is runtime-gated for voice (`data-live-voice-mode="off"`) and must not ship the retired static welcome bubble.

Verification already run locally:

- `npm test`
- `npm run typecheck`
- `npm run build`
- `npm run lint`
- `npx playwright test e2e/smoke.spec.ts`
- `pytest backend/tests/test_support_intelligence_config.py -q`
- `pytest backend/tests/test_next_phase_max_push.py backend/tests/test_ai_config_migration.py -q`

Live 178 validation already observed:

- `scripts/smoke/public_webchat_smoke.py --base-url https://www.leakle.com` passed against `nexusdesk/helpdesk:manual-agent-console-demo-hygiene-20260701T231728Z`.
- Public `/api/webchat/fast-reply` returned `reply_source=private_ai_runtime`, `ai_generated=true`, and a non-empty reply.
- Browser-level Playwright smoke proved desktop auto-open, text send, rendered bot reply, no relevant console warnings/errors, and mobile auto-open/input visibility.
- Browser-level Playwright smoke also proved the retired static welcome bubble and forced/clipped `VOIP Call` edge-card entry are no longer visible while voice runtime-config is disabled/mock-backed.
- Support-intelligence rollback smoke inside the 178 app container proved status dictionary draft/publish uses `source=nexus_ai_config_resources` and rolls back without persisting smoke data.
- WhatsApp candidate runtime audit passed: running candidate container envs have no retired vendor markers, app OpenClaw env/file counts are zero, and sidecar read-only smoke passed.
- 178 cleanup left only the current candidate app, worker, and WhatsApp sidecar containers. The only remaining helpdesk image is `manual-agent-console-demo-hygiene-20260701T231728Z`.

### P1: Lightweight Agent Surface

- Re-group AppShell navigation around support-agent tasks first.
- Remove WebChat first-screen roadmap/model cards from the agent path.
- Replace them with a compact live work strip: current queue, total WebChat conversations, closed count, realtime state, selected ticket.
- Keep WebChat route, APIs, permissions, and side effects unchanged.
- Update frontend contract tests so the expected shell is the lightweight agent console, not the old foundation model.

Acceptance:

- `webapp/tests/webchat-inbox-v5-contract.test.mjs` proves WebChat still uses real APIs and does not fake non-WebChat traffic.
- Typecheck and targeted webapp tests pass.
- Public smoke after deployment still returns `reply_source=private_ai_runtime`.

### P2: Support Intelligence Without Legacy Bridge

- Replace retired status-dictionary bridge endpoints with DB-backed CRUD/publish/readiness contracts.
- Keep redaction and audit evidence.
- Make natural-language config compile into structured runtime policy, but only publish when tests pass.

Acceptance:

- No UI default path depends on `legacy_status_dictionary_runtime_bridge_retired`.
- Config overview shows real source, version, and publish state.
- `backend/tests/test_support_intelligence_config.py` proves draft, publish, config summary, and API route functions are DB-backed.

### P3: Native WhatsApp Into The Same Inbox

- Keep native sidecar session untouched during unrelated UI work.
- Add WhatsApp conversation ingestion only when it can produce the same agent contract as WebChat: claim, release, reply, resolve, audit, unread, AI suspended.
- Do not show WhatsApp customer rows in the agent inbox until the adapter returns real conversations.

Acceptance:

- WhatsApp rows identify native source and sidecar health.
- A WebChat regression smoke and a WhatsApp native smoke both pass before routing support staff to it.

### P4: Manager Simulations Inspired By DSP

- Add planning views only under manager/Ops capabilities.
- Use DSP-style inputs/results for staffing, SLA pressure, queue volume, and knowledge-gap impact.
- Keep the daily agent console clean.

Acceptance:

- Simulation inputs are explicit.
- Outputs are traceable and cannot be mistaken for live customer actions.

### P5: 178 Production Drift Cleanup

- Remove residual legacy nginx routes and host services only after replacement checks are green.
- Preserve current WhatsApp sidecar sessions unless intentionally migrated.
- Keep one active candidate project and document release metadata.

Acceptance:

- No active public route points to a dead local listener.
- `docker compose ps`, nginx config, and public `/healthz` agree on the active candidate.
- Cleanup is recorded in an ops note without secrets.

## GitHub Gates

For every PR in this track:

- Contract tests for API wiring and UI promises.
- Typecheck for the webapp.
- Public smoke workflow for the deployed candidate before cleanup.
- Release metadata consistency check.
- Manual production cleanup checklist when 178 host services are touched.

Local verification is acceptable for targeted development, but GitHub Actions should be the source of record before merge or production cleanup.
