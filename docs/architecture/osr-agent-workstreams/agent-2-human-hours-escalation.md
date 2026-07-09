# Agent 2 — Human Hours and Escalation Integration

Base branch: `feat/nexus-osr-runtime-foundation` (#451)

## Mission

Make Nexus decide correctly when a customer needs a human: human online -> handoff; human offline -> ticket creation path; complaint/compensation/legal threat -> can-do first, then escalate according to policy.

This PR should integrate existing handoff mechanics with the new OSR policy layer, without touching customer-visible copy beyond safe offline/handoff notices.

## Current facts to use

Existing repository code:

- `backend/app/services/webchat_handoff_service.py`
- `backend/app/services/webchat_ai_service.py`
- `backend/app/services/nexus_osr/policies.py`
- `backend/app/services/nexus_osr/persistence.py`
- `backend/app/services/nexus_osr/auto_ticket_service.py`
- `backend/app/models_osr.py`
- `backend/app/models.py`
- `backend/app/webchat_models.py`

#451 already adds `HumanHoursPolicyRecord`, `EscalationPolicyRecord`, policy resolution, and auto-ticket service. Use those directly.

## Scope

Implement a shared decision service, preferably:

- `backend/app/services/nexus_osr/escalation_orchestration_service.py`

Responsibilities:

1. Load `HumanHoursPolicyRecord` by country/channel/queue.
2. Load `EscalationPolicyRecord` by country/channel.
3. Evaluate inbound customer message and AI attempt count.
4. Decide one of:
   - `continue_ai`
   - `request_handoff`
   - `create_ticket_offline`
   - `create_ticket_customer_cannot_wait`
   - `create_ticket_high_risk`
5. When online, call existing `request_webchat_handoff()`.
6. When offline, call `create_or_reuse_ticket_from_case_context()`.
7. Write TicketEvent / WebchatEvent / RuntimeDecisionAudit where appropriate.

## Required behavior

- If escalation required and human online: request WebChat handoff.
- If escalation required and human offline: create/reuse ticket and generate offline notice.
- If customer explicitly cannot wait: create/reuse ticket.
- If compensation/refund/legal threat reaches configured threshold: handoff or ticket.
- Before threshold: AI may continue can-do response, but must not promise compensation/refund resolution.

## Hard rules

Do not:

- Hard-code country priorities.
- Hard-code language priorities.
- Create long-term customer memory.
- Modify WhatsApp routing in this PR.
- Bypass existing `webchat_handoff_service.py`.
- Send customer-visible text outside `CustomerVisibleMessageService`.

Do:

- Use `HumanHoursPolicyRecord` and `EscalationPolicyRecord`.
- Make every decision auditable.
- Keep policy defaults safe when no policy is configured.
- Add tests for online/offline/holiday/customer wait/compensation/legal threat.

## Expected files likely touched

- `backend/app/services/nexus_osr/escalation_orchestration_service.py`
- `backend/app/services/webchat_ai_service.py` only if needed for integration hooks
- `backend/tests/test_nexus_osr_escalation_orchestration.py`

Coordinate with Agent 1 if both touch `webchat_ai_service.py`. Agent 2 should prefer adding a service and tests first, then a small integration hook.

## Acceptance tests

1. Human online + handoff required -> `WebchatHandoffRequest` is created and AI is suspended via existing service.
2. Human offline + handoff required -> Ticket is created/reused and CaseContext is updated.
3. Holiday is treated as offline.
4. Customer wait timeout creates/reuses ticket.
5. Compensation before max attempts continues AI with safe escalation policy warning.
6. Compensation at/after max attempts escalates.
7. Legal threat escalates immediately.
8. No raw phone/email/tracking in audit payload.

## Prompt for the agent

You are Agent 2 for Nexus OSR. Your task is to implement human-hours and escalation orchestration based on #451. Use existing `webchat_handoff_service.py`, `HumanHoursPolicyRecord`, `EscalationPolicyRecord`, and `auto_ticket_service.py`. Do not invent a new handoff system. Do not hard-code country/language behavior. Build a service that decides online handoff vs offline ticket creation vs continue-AI based on policy and writes safe audit events. Add tests proving online, offline, holiday, compensation, legal threat, and customer-wait paths work.