# Agent 4 — WhatsApp Group Routing and Operations Dispatch

Base branch: `feat/nexus-osr-runtime-foundation` (#451)

## Mission

Implement the operations-routing layer: once a Ticket/CaseContext exists, Nexus routes the operational case to the correct WhatsApp operations group by country and issue type, with safe fallback and audit. This PR must not modify the WhatsApp sidecar protocol or customer-visible reply behavior.

## Current facts used

Existing code and #451 foundation:

- `backend/app/models_osr.py` -> `WhatsAppRoutingRuleRecord`
- `backend/app/services/nexus_osr/persistence.py` -> `resolve_whatsapp_routing_rule()`
- `backend/app/services/nexus_osr/case_context.py`
- `backend/app/services/nexus_osr/auto_ticket_service.py`
- `backend/app/models.py` -> `Ticket` / `TicketEvent`

Repository search found customer-visible WhatsApp/outbound paths and historical native sidecar work, but no stable OSR operations-group dispatch abstraction. Therefore Agent 4 defines a narrow internal operations dispatch contract and records pending dispatch events when no dispatcher is injected.

## Implemented scope

Primary service:

- `backend/app/services/nexus_osr/whatsapp_routing_service.py`

Responsibilities:

1. Accept `Ticket + CaseContext`.
2. Resolve `WhatsAppRoutingRuleRecord` by country, issue type, and channel.
3. Apply routing fallback order.
4. Build safe group message from allowed fields only.
5. Dispatch through an injected internal `WhatsAppGroupDispatcher` protocol if available.
6. If no dispatcher exists, create a pending operations dispatch event and do not call sidecar.
7. Write `TicketEvent` with safe routing/dispatch payload.
8. Mark and persist `CaseContext.routed_group_key` when pending/dispatched/fallback succeeds.
9. Support `fallback_group_id` as a provider-side fallback target.
10. Prevent duplicate pending dispatch for the same ticket/case/issue route.

## OSROperationsDispatchQueue contract

Agent 4 currently represents the operations dispatch queue through safe `TicketEvent` payloads instead of introducing a new table in this PR. The internal queue item is `OSROperationsDispatchQueueItem`.

Supported states:

- `pending` — routing resolved, no injected dispatcher exists, operator/worker can pick up later.
- `dispatched` — injected dispatcher accepted primary provider group dispatch.
- `failed` — injected dispatcher failed with non-retryable error.
- `retryable` — injected dispatcher failed with retryable error.
- `cancelled` — routing disabled or intentionally not executable.
- `fallback_used` — primary provider group failed, fallback provider group succeeded.

Every queue event includes:

- `dispatch_key`
- `dispatch_status`
- `destination_group_key`
- `provider_group_id_hash`
- optional `fallback_group_key`
- optional `fallback_provider_group_id_hash`
- optional `attempted_group_key`
- `fallback_used`
- `retryable`
- safe message hash/preview

It does not expose raw provider group identifiers.

## Group identity model

`WhatsAppRoutingRuleRecord` from #451 still stores:

- `destination_group_id`
- `fallback_group_id`

In Agent 4, these are treated as **provider group ids**, not business display ids.

Business/audit surfaces use derived keys:

- `destination_group_key`: `{channel}:{country}:{issue}:destination`
- `fallback_group_key`: `{channel}:{country}:{issue}:fallback`

TicketEvent payloads store only provider id hashes, not raw provider ids.

## Routing fallback order

The routing service attempts scopes in this order:

1. exact `country + issue + channel`
2. `country + general + channel`
3. `GLOBAL + issue + channel`
4. `GLOBAL + general + channel`

If a matching scope is disabled, routing stops safely with `routing_disabled` and no later fallback is used. If no enabled or disabled scope exists, it records `routing_not_configured`. No rule means no send.

## Message template rules

Allowed fields:

- `ticket_no`
- `issue_type`
- `country_code`
- `safe_tracking_reference`
- `customer_claim_summary`
- `missing_info`
- `case_status`

Forbidden fields:

- raw tracking number
- raw phone
- raw email
- raw address
- raw MCP payload
- raw customer text beyond redacted summary
- raw provider group id

Unknown template fields render as `[unavailable]`.

## Hard rules

Do not:

- Hard-code countries or groups in Python.
- Send to groups without `WhatsAppRoutingRuleRecord`.
- Bypass TicketEvent audit.
- Bypass CaseContext update.
- Expose raw PII, raw tracking, raw address, raw MCP payload, or raw provider group id.
- Touch customer-visible reply behavior.
- Touch native WhatsApp sidecar protocol.

Do:

- Keep routing rule-driven.
- Keep pending dispatch event path safe when no dispatcher exists.
- Add fallback group behavior.
- Make no-rule behavior safe: record `routing_not_configured` but do not fail customer-visible flow.
- Make idempotency safe: same `ticket + case_context + issue_type + country + channel` should not create duplicate active pending/dispatched/retryable dispatch events.

## Acceptance tests

Implemented test coverage:

1. Matching country + issue route selects destination group key.
2. No dispatcher writes pending dispatch event.
3. Disabled exact rule does not route, even if later fallback exists.
4. No matching rule writes `routing_not_configured`.
5. Fallback rule order covers country-general, global-issue, and global-general.
6. Primary dispatch failure can use fallback group.
7. Retryable dispatch failure records `retryable` queue state.
8. CaseContext is marked routed after successful/pending/fallback dispatch.
9. TicketEvent stores safe routing/dispatch payload.
10. Group message and event payload do not contain raw tracking/phone/email/address/provider group id.
11. Unknown template fields do not leak; they render as `[unavailable]`.
