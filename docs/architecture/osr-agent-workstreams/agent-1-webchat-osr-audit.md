# Agent 1 — WebChat OSR Audit Integration

Base branch: `main` after #451 Nexus OSR runtime foundation merge.

## Mission

Wire the OSR foundation into the existing WebChat AI runtime as a **non-blocking audit sidecar**. Do not change customer-visible reply behavior yet. The goal is to make every WebChat AI turn produce a persistent OSR Case Context and RuntimeDecisionAudit so later PRs can safely enable hard enforcement.

## Current facts to use

The repository already has:

- `backend/app/services/webchat_ai_service.py`
- `backend/app/services/nexus_osr/runtime_bridge.py`
- `backend/app/services/nexus_osr/persistence.py`
- `backend/app/services/nexus_osr/runtime_decision_contract.py`
- `backend/app/services/nexus_osr/case_context.py`
- `backend/app/services/tracking_fact_schema.py`
- `backend/app/services/webchat_debug_bundle_service.py`
- `backend/app/models_webchat_debug.py`
- `backend/app/models_osr.py`

#451 has been merged into `main`. This PR should build on that foundation instead of creating parallel concepts.

## Scope

Implement a safe WebChat audit integration:

1. Locate the WebChat AI reply completion path in `webchat_ai_service.py` after tracking fact lookup, runtime result, final body, metadata, and customer-visible message creation.
2. Build or update `CaseContextRecord` using `build_case_context_from_webchat()`.
3. Convert existing `TrackingFactResult` into OSR evidence using `evidence_from_tracking_fact()`.
4. Map the existing reply outcome into a `RuntimeDecision`.
5. Evaluate with `evaluate_runtime_decision()`.
6. Persist `RuntimeDecisionAuditRecord` using `audit_existing_webchat_runtime_decision()`.
7. Add the audit id and case context summary to safe metadata / event payload / debug bundle surfaces.
8. Add tests proving customer-visible reply content is unchanged.

## Business reply type mapping

Use conservative mapping first:

- `tracking_fact.fact_evidence_present == true` and tracking intent present -> `tracking_status_answer`
- KB/direct grounding answer without live tracking claim -> `knowledge_answer`
- `runtime_handoff_required == true` -> `handoff_notice`
- empty/suppressed/null reply -> `no_answer`
- otherwise -> `clarification` or `knowledge_answer` only if evidence supports it

If uncertain, choose `clarification` and write audit warning instead of claiming a factual answer.

## Hard rules

Do not:

- Change generated customer reply text.
- Change provider/runtime behavior.
- Enable hard blocking from OSR evaluation yet.
- Execute new tools from this PR.
- Touch WhatsApp sidecar.
- Add new AI provider behavior.

Do:

- Make the audit best-effort and fail-closed to logging, not to customer-visible send.
- Sanitize metadata. No raw tracking numbers, phone, email, token, prompt, or raw tool payload.
- Preserve existing `CustomerVisibleMessageService` as the only customer-visible send boundary.

## Expected files likely touched

- `backend/app/services/webchat_ai_safe_service.py`
- `backend/app/services/webchat_debug_bundle_service.py`
- `backend/app/services/webchat_osr_audit_service.py`
- `backend/tests/test_webchat_osr_audit_integration.py`

Avoid touching unrelated frontend files.

## Acceptance tests

Add tests covering:

1. WebChat AI reply with trusted tracking fact creates `RuntimeDecisionAuditRecord` with `allowed=true`.
2. The same path creates/updates `CaseContextRecord`.
3. Existing customer-visible reply body is unchanged.
4. Metadata/debug bundle includes safe OSR audit reference.
5. OSR audit failure does not prevent existing customer-visible message creation.
6. Raw tracking number is not present in OSR audit metadata.

## Prompt for the agent

You are Agent 1 for Nexus OSR. Your task is to integrate OSR audit into the existing WebChat AI path without changing customer-visible behavior. Use the OSR foundation now merged into `main`. Work only from current repository facts. Do not invent a new runtime or memory system. Implement a non-blocking audit sidecar that creates CaseContextRecord and RuntimeDecisionAuditRecord for each WebChat AI turn. Preserve CustomerVisibleMessageService as the only outbound boundary. Add tests proving existing replies are unchanged and OSR audit is persisted safely. If uncertain, audit only; do not block or execute tools.
