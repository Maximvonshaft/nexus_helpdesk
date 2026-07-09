# Agent 4 — WhatsApp Group Routing and Operations Dispatch

Base branch: `feat/nexus-osr-runtime-foundation` (#451)

## Mission

Implement the operations-routing layer: once a Ticket/CaseContext exists, Nexus should route the operational case to the correct WhatsApp group based on country and issue type, with safe fallback and audit. This PR should not modify the WhatsApp sidecar protocol unless the current repository already exposes a safe dispatch abstraction.

## Current facts to use

Existing code and #451 foundation:

- `backend/app/models_osr.py` -> `WhatsAppRoutingRuleRecord`
- `backend/app/services/nexus_osr/persistence.py` -> `resolve_whatsapp_routing_rule()`
- `backend/app/services/nexus_osr/case_context.py`
- `backend/app/services/nexus_osr/auto_ticket_service.py`
- `backend/app/models.py` -> Ticket / TicketEvent
- Existing WhatsApp/external-channel code in the repository. Search first before adding new abstractions.

## Scope

Implement a routing service, preferably:

- `backend/app/services/nexus_osr/whatsapp_routing_service.py`

Responsibilities:

1. Accept Ticket + CaseContext.
2. Resolve `WhatsAppRoutingRuleRecord` by country_code + issue_type + channel.
3. Build safe group message from template.
4. Dispatch via existing WhatsApp/external-channel abstraction if available.
5. If no real dispatcher exists, create a pending dispatch/audit event and leave a clean interface for sidecar integration.
6. Write TicketEvent with routing decision.
7. Mark CaseContext as routed.
8. Add tests for routing/fallback/no-rule/disabled-rule.

## Message template rules

Allowed fields:

- ticket_no
- issue_type
- country_code
- safe_tracking_reference
- customer_claim_summary redacted
- missing_info
- case_context status

Forbidden fields:

- raw tracking number
- raw phone
- raw email
- raw address
- raw MCP payload
- raw customer text beyond redacted summary

## Hard rules

Do not:

- Hard-code Montenegro/Macedonia/Switzerland routing in Python.
- Send to groups without `WhatsAppRoutingRuleRecord`.
- Bypass TicketEvent audit.
- Bypass CaseContext update.
- Expose raw PII or raw tracking in group messages.
- Touch customer-visible reply behavior.

Do:

- Use existing WhatsApp/external-channel code if present.
- Keep routing rule-driven.
- Add fallback group behavior.
- Make no-rule behavior safe: record `routing_not_configured` but do not fail customer-visible flow.

## Expected files likely touched

- `backend/app/services/nexus_osr/whatsapp_routing_service.py`
- Possibly existing external-channel/WhatsApp service only after repository search
- `backend/tests/test_nexus_osr_whatsapp_routing_service.py`

Coordinate with Agent 3 if both work on TicketEvent/action audit.

## Acceptance tests

1. Matching country + issue route selects destination group.
2. Disabled rule does not route.
3. No matching rule writes safe not-configured event.
4. Fallback group is used when dispatcher reports failure.
5. CaseContext is marked routed after successful/pending dispatch.
6. TicketEvent stores safe routing payload.
7. Group message does not contain raw tracking/phone/email.

## Prompt for the agent

You are Agent 4 for Nexus OSR. Your task is to implement WhatsApp operations routing from Ticket + CaseContext using `WhatsAppRoutingRuleRecord`. Search the repository for existing WhatsApp/external-channel dispatch abstractions before adding anything. Do not hard-code countries or groups. Do not modify customer-visible reply behavior. Build a safe routing service that creates auditable TicketEvents, updates CaseContext, and either dispatches through existing infrastructure or records a pending dispatch if no safe dispatcher exists. Add tests for routing, fallback, no-rule, disabled-rule, and PII redaction.