# Nexus T1 Tracking Truth Source Readiness

Date: 2026-07-08
Repository: `Maximvonshaft/nexus_helpdesk`
Branch prepared: `agent/t1-tracking-truth-source-readiness-20260708`
Audit target: current `main` after the release-stabilization and cleanup docs lane.

This document is a readiness report only. It does not implement T1 code, does not revive PR #395, and does not touch runtime, deployment, WhatsApp, frontend, workflow, or customer-visible outbound code.

## Executive Summary

T1 should proceed as a small backend contract PR after M1 Runtime Context Guard is merged. The current tracking fact lane is close to the desired shape but still has contract gaps:

1. `WEBCHAT_TRACKING_FACT_SOURCE` currently accepts `speedaf_api`, `speedaf_track_query`, and `speedaf_hybrid`. It does not accept `openclaw_bridge`.
2. The intended primary source is `speedaf.order.query`, backed by `/open-api/mcp/order/query`.
3. `/open-api/express/track/query` is intended as enrichment, but current hybrid lookup can return express history as the effective fact when primary has no trusted evidence. That violates the T1 truth-source rule.
4. Runtime v3 currently requires `grounding.used_sources` for any `answer`, but it does not require a tracking-specific tool source for tracking answers.
5. KB-only live tracking answers are partially guarded by fact gates and runtime context policy, but the v3 contract does not make this structurally impossible.
6. There is no explicit `tool_result > KB` conflict trace or quality event named `tool_kb_conflict` / `tool_over_kb_resolution`.
7. The tracking service returns structured `TrackingFactResult`; it has a prompt-summary helper used as provider input, but it does not directly send customer-visible text.

T1 should enforce the missing contract without changing `CustomerVisibleMessageService`, AI Runtime service, provider routing, WhatsApp, WebChat UI, deployment, or knowledge schema.

## Current Code Audit

### Settings allow-list

Current `Settings` reads `WEBCHAT_TRACKING_FACT_SOURCE` with default `speedaf_api`. The current normalization allow-list is:

- `speedaf_api`
- `speedaf_track_query`
- `speedaf_hybrid`

The error message also lists only these three values. `openclaw_bridge` is not currently accepted. Legacy `external_channel_bridge` is covered as unsupported in tests.

Audit implication: T1 must decide whether `openclaw_bridge` is still a supported symbolic value. If it is supported, settings and error text must be updated. If it is not supported, the proposed test name should assert explicit rejection instead of acceptance.

### Tracking fact service routing

`lookup_tracking_fact` routes by configured source:

- `speedaf_api` -> `lookup_speedaf_tracking_fact`
- `speedaf_track_query` -> `lookup_speedaf_track_history_fact`
- `speedaf_hybrid` -> `lookup_speedaf_hybrid_tracking_fact`
- unknown -> structured `TrackingFactResult(ok=False, tool_status="unsupported_source", failure_reason="unsupported_tracking_fact_source")`

`_tracking_tool_identity` maps `speedaf_api` and `speedaf_hybrid` to `speedaf.order.query`, while `speedaf_track_query` maps to `speedaf.express.track.query`.

Audit implication: the primary tool identity exists, but T1 should make it explicit in tests and runtime metadata.

### Primary vs enrichment source

`lookup_speedaf_hybrid_tracking_fact` documents the intended source split:

- current status comes from `/open-api/mcp/order/query`;
- track history comes from `/open-api/express/track/query`;
- history failures are non-fatal.

`merge_speedaf_hybrid_tracking_fact` preserves primary current status when both primary and history are trusted. It returns primary unchanged if primary lacks trusted evidence, and it keeps `status` / `status_label` from the primary result when merging.

Audit implication: the merge function is safe, but the lookup orchestration below it has a gap.

### Hybrid fallback risk

Current hybrid lookup does this when primary is not trusted:

1. Call `lookup_speedaf_track_history_fact`.
2. If history is `ok` and has `fact_evidence_present`, return the history result.
3. Otherwise return primary.

There is also a test named `test_hybrid_lookup_falls_back_to_track_query_when_primary_has_no_evidence` that asserts this behavior.

Audit conclusion: this violates T1. Express history can become the effective current tracking fact when `/open-api/mcp/order/query` has no evidence. T1 must change this to preserve the primary failure/no-evidence result while optionally exposing sanitized `recent_events` / enrichment metadata separately.

### WebChat tracking fact flow

`process_webchat_ai_reply_job` performs tracking lookup before AI generation. It calculates `fact_evidence_present` only when the fact exists, has `fact_evidence_present`, and is PII-redacted. It passes tracking metadata into message metadata and event payload.

`_generate_ai_reply` builds `tracking_fact_summary` only when the tracking fact has evidence and PII redaction. It then passes:

- `tracking_fact_summary`
- `tracking_fact_metadata`
- `tracking_fact_evidence_present`
- `runtime_context`

to the runtime reply generator.

Audit implication: the WebChat service has the right high-level pattern, but it inherits the hybrid fallback risk from the lower-level tracking fact source.

### Runtime context tracking boundary

`ai_runtime_context.build_webchat_runtime_context` includes a safety policy stating:

- live parcel status requires `tracking_fact_evidence_present=true` and a trusted `tracking_fact_summary`;
- knowledge documents must not be used to infer current parcel status;
- SOP / FAQ / policy chunks must not be treated as live shipment evidence;
- knowledge text must not override trusted tracking facts.

Audit implication: the policy text is present. T1 should add structural tests so this is not only prompt-level guidance.

### Runtime v3 answer / used_sources contract

Current `validate_ai_reply_v3_payload` enforces:

- valid reply type;
- `null_reply` cannot be customer-visible;
- `answer` requires non-empty `used_sources`;
- `answer` blocks non-empty `unsupported_claims`;
- `handoff_notice` also blocks unsupported claims.

It does not enforce that a tracking answer must include a tool source such as `speedaf.order.query`.

Audit implication: today, a v3 `answer` can satisfy the contract with a KB source only. That is acceptable for policy answers, but not for live tracking answers. T1 should add a helper or validator for tracking-specific answers.

### Output contract / fact gate

`OutputContracts.check_security_rules` blocks `intent=tracking` without tracking number and blocks tracking status output when `evidence_present=false`. It also blocks parcel-status language without trusted evidence unless the reply is a safe grounded business-policy answer.

`webchat_fact_gate` blocks definite operational claims such as delivered/out-for-delivery/ETA language when no business or tool evidence exists.

Audit implication: current guards are useful but not sufficient for T1 because they do not prove `used_sources` contains the primary tracking tool and do not encode `tool_result > KB` conflict telemetry.

### Existing tests

Relevant current tests include:

- settings accepts `speedaf_api` and rejects unknown values;
- tracking fact service routes to `speedaf_api`;
- legacy bridge source is unsupported;
- hybrid merge preserves primary current status;
- hybrid merge ignores failed history;
- hybrid merge ignores history when primary is not trusted;
- current hybrid lookup falls back to express history when primary has no evidence;
- tracking prompt redacts full waybill numbers and PII;
- fact gate blocks delivered/status claims without evidence;
- v3 answer requires any `used_sources` and blocks unsupported claims.

Audit implication: T1 mostly needs to convert the hybrid fallback test from accepted behavior into a prohibited behavior and add v3 tracking-specific tool-source tests.

## Current Risks

1. **Express history can become current status.** Current hybrid lookup can return `speedaf.express.track.query` as the effective fact when primary has no evidence.
2. **Settings allow-list drift.** `openclaw_bridge` is not accepted even though prior designs mention it. T1 must explicitly decide accept vs reject.
3. **V3 tracking source is too weak.** `used_sources` only needs to be non-empty; a KB-only live tracking answer can satisfy the generic v3 answer rule.
4. **KB conflict is policy text, not auditable event.** Current runtime context says KB must not override tracking facts, but no dedicated `tool_kb_conflict` / `tool_over_kb_resolution` trace is present.
5. **Prompt-summary text can be misunderstood by future maintainers.** `TrackingFactResult.prompt_summary()` is internal provider context, not customer text. T1 should preserve this boundary in tests.
6. **M1 overlap risk.** PR #444 / M1 touches runtime context and tracking answer policy. T1 should wait for M1 merge and rebase from the post-M1 main.

## T1 Contract

T1 must define and enforce the following contract:

1. Current parcel status must come from the primary tracking tool.
2. Primary source is `speedaf.order.query`, backed by `/open-api/mcp/order/query`.
3. Track history / express track query is enrichment only.
4. Express history must not override or replace primary current status.
5. If primary has no trusted evidence, the system must not answer live current status.
6. If express history has events while primary has no evidence, the system may expose structured `recent_events` / enrichment metadata, but must not package that as current status.
7. V3 tracking answers must include a tool source identifying the primary tracking tool.
8. KB-only live tracking answers must be blocked.
9. Previous AI replies are not tracking facts.
10. Customer claims are not tracking facts.
11. Tool result beats KB when both exist and conflict.
12. Tool/KB conflict resolution must record a trace flag or quality event:
    - `tool_kb_conflict`
    - `tool_over_kb_resolution`
13. Tracking service must return structured facts only. It must not generate customer-visible natural language or bypass outbound governance.

## Proposed Small PR

PR title:

`fix: enforce tracking tool-source contract for Speedaf facts`

Branch:

`agent/t1-tracking-contract-speedaf-facts`

Base:

post-M1 `main`, not PR #395.

### Allowed scope

- settings allow-list / settings tests
- tracking fact service / Speedaf tracking fact source
- AI reply v3 validation helper if needed
- runtime context tracking source metadata
- focused backend tests

### Do not change

- `CustomerVisibleMessageService`
- outbound contract mainline
- WhatsApp sidecar
- WebChat UI / frontend
- Email / WebCall old lane
- knowledge schema
- AI Runtime service
- provider runtime routing
- deployment / tags / workflows

### Proposed implementation slices

1. Settings: add or explicitly reject `openclaw_bridge`, then align the regression tests and error message.
2. Hybrid source: remove primary-no-evidence fallback to express history as current fact.
3. Enrichment metadata: optionally carry safe express events as `recent_events` / `enrichment_events` without setting current status or `fact_evidence_present=true` for current status.
4. V3 tracking contract: add a small validation helper, for example `validate_tracking_tool_source_contract(...)`, that checks tracking answers for a primary tool source.
5. Runtime metadata: ensure tracking answer traces identify `source_type=tool` and `tool_name=speedaf.order.query` or equivalent primary tool identity.
6. Conflict trace: record `tool_kb_conflict` and `tool_over_kb_resolution` when both tool and KB are present and disagree on live parcel status.
7. Tests: add the focused tests below before widening any implementation.

## Test Plan

### Settings

- `test_tracking_source_allowlist_accepts_speedaf_hybrid`
  - Purpose: prove `WEBCHAT_TRACKING_FACT_SOURCE=speedaf_hybrid` is valid.

- `test_tracking_source_allowlist_includes_openclaw_bridge_if_still_supported`
  - Purpose: if the product still supports `openclaw_bridge`, prove settings accepts it and error text includes it.
  - Alternative: rename to `test_tracking_source_allowlist_rejects_openclaw_bridge_if_retired` if the legacy bridge is intentionally retired.

### Primary / enrichment contract

- `test_speedaf_hybrid_preserves_primary_current_status`
  - Purpose: prove primary `/mcp/order/query` status remains current status even when express history has newer or conflicting status text.

- `test_speedaf_hybrid_track_history_is_enrichment_only`
  - Purpose: prove express history contributes events only and cannot change `status`, `status_label`, `tool_name`, or primary current-source identity.

- `test_speedaf_hybrid_primary_without_evidence_does_not_create_current_status`
  - Purpose: replace the current fallback behavior. When primary lacks evidence, history may be returned as structured enrichment metadata but must not set current status or `fact_evidence_present=true` for live status.

### V3 / source contract

- `test_v3_tracking_answer_requires_tool_source`
  - Purpose: a v3 tracking answer with non-empty KB source but no tool source must fail.

- `test_v3_tracking_answer_blocks_kb_only_source`
  - Purpose: live tracking answer using only `knowledge:*` or `kb:*` in `used_sources` must be blocked.

- `test_v3_tracking_answer_accepts_primary_tool_source`
  - Purpose: live tracking answer with `source_type=tool` / `tool_name=speedaf.order.query` or equivalent canonical string passes when unsupported claims are empty.

### Conflict resolution

- `test_tracking_tool_result_overrides_kb_conflict`
  - Purpose: when KB says a status/ETA/policy-like parcel claim conflicts with tool current status, the system uses the tool result.

- `test_tracking_tool_kb_conflict_trace_recorded`
  - Purpose: prove trace/event contains `tool_kb_conflict` and `tool_over_kb_resolution`.

### Service boundary / privacy

- `test_tracking_service_does_not_generate_customer_visible_text`
  - Purpose: prove tracking service returns `TrackingFactResult` / metadata only; no queue/outbound/customer-visible text path is invoked.

- `test_tracking_fact_redacts_raw_tracking_number_in_trace`
  - Purpose: prove tool traces and metadata expose only hash/suffix/safe reference, not raw tracking numbers.

## Files Likely To Change

Expected T1 implementation files:

- `backend/app/settings.py`
- `backend/app/services/tracking_fact_service.py`
- `backend/app/services/speedaf/tracking_fact_source.py`
- `backend/app/services/tracking_fact_schema.py` if enrichment metadata needs a typed field
- `backend/app/services/ai_reply_contract.py` or a narrow helper module if v3 tracking source validation belongs near the v3 contract
- `backend/app/services/ai_runtime_context.py` only if post-M1 metadata needs a minimal source flag
- `backend/tests/test_speedaf_settings.py`
- `backend/tests/test_tracking_fact_service_speedaf_source.py`
- `backend/tests/test_speedaf_hybrid_tracking_source.py`
- `backend/tests/test_ai_customer_visible_contracts.py`
- `backend/tests/test_webchat_runtime_ai_service.py` if runtime source/KB conflict logic lands there after M1

## Files That Must Not Change

- `backend/app/services/customer_visible_message_service.py`
- `backend/app/services/message_dispatch.py`, unless a test imports existing behavior only
- `connectors/whatsapp-sidecar/**`
- `webapp/**`
- `frontend/**`
- `.github/workflows/**`
- `deploy/**`
- `backend/alembic/**`
- knowledge model/schema/migration files
- AI Runtime provider adapters, unless only existing tests import them without changing implementation

## Merge Gates

Before opening T1 implementation PR:

1. M1 Runtime Context Guard is merged.
2. T1 branch is cut from post-M1 `main`.
3. No #395 branch revive, no cherry-pick from old PRs.

Before merging T1:

```bash
PYTHONPATH=backend python -m pytest -q backend/tests -k "speedaf or tracking_fact or hybrid"
PYTHONPATH=backend python -m pytest -q backend/tests/test_ai_customer_visible_contracts.py -k "tracking or source or v3"
PYTHONPATH=backend python -m pytest -q backend/tests/test_webchat_runtime_ai_service.py -k "tracking or source or conflict"
python -m compileall -q backend/app
git diff --check
```

Required evidence:

- `speedaf_hybrid` is accepted.
- `openclaw_bridge` decision is explicit.
- primary order query is the only current-status source.
- express track query is enrichment only.
- primary no-evidence cannot produce live current status.
- v3 tracking answer requires primary tool source.
- KB-only live tracking answer is blocked.
- tool/KV conflict trace or quality event exists.
- no customer-visible bypass is introduced.

## Open Questions

1. Is `openclaw_bridge` still a legitimate value for `WEBCHAT_TRACKING_FACT_SOURCE`, or is it retired? Current main rejects legacy bridge-like values.
2. Should express history enrichment be represented inside `TrackingFactResult` as a typed field such as `recent_events`, or should it remain in metadata only to avoid semantic confusion?
3. Where should the v3 tracking source validator live after M1: `ai_reply_contract.py`, runtime decision validation, or a small dedicated tracking contract helper?
4. What is the canonical tool-source string for v3 `used_sources`: `tool:speedaf.order.query`, `speedaf.order.query`, or a structured source object serialized into the contract?
5. Should `tool_kb_conflict` be a `TicketEvent`, `WebchatEvent`, runtime trace field, or all of these?
6. After M1, does its runtime context policy already implement previous-AI-reply and customer-claim non-evidence classification? T1 should consume that result, not duplicate it.
