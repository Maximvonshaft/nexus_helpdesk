# 13 — API Contract Map

## Status

Proposed. This document freezes the frontend-facing API contract assumptions for the frontend upgrade. It is a planning artifact and does not change backend behavior.

## Purpose

The frontend upgrade must not accidentally change API paths, auth behavior, public/private boundaries, or request/response semantics. This contract map lists the API surfaces currently used or expected by the frontend upgrade plan.

## Contract principles

1. Preserve existing API paths during frontend refactor.
2. Preserve admin token behavior during frontend refactor.
3. Keep public WebChat visitor APIs separate from authenticated admin APIs.
4. Do not expose internal ticket ids through public WebChat visitor responses.
5. Add new APIs only through a separate reviewed backend/API PR.
6. Frontend domain API splitting must wrap existing endpoints, not mutate endpoint semantics.

## Auth API

### POST `/api/auth/login`

Used by:

- login page
- session bootstrap after credential submission

Expected behavior:

- accepts username/password payload
- returns access token and authenticated user payload
- 401 on invalid credentials

Frontend constraints:

- public request; no bearer token required
- failure should show user-safe login error
- must not clear unrelated browser state except auth token handling

### GET `/api/auth/me`

Used by:

- session validation
- AppShell / route guards
- role-based navigation

Expected behavior:

- requires admin bearer token
- returns authenticated user
- 401 means token expired or invalid

Frontend constraints:

- global 401 handling must clear admin token once
- UI should redirect to login without throwing unrecoverable React errors

## Ticket / Workspace API

### GET `/api/lite/meta`

Used by:

- Workspace metadata
- status options
- users list
- teams/markets where included

Frontend constraints:

- cacheable through TanStack Query
- failure should not break the entire console if ticket detail can still load

### GET `/api/lite/cases`

Used by:

- Workspace queue

Known query usage:

- `q`
- `status`

Frontend constraints:

- frontend may apply additional client-side market filtering until backend supports server-side filter
- preserve response shape expected by current queue cards

### GET `/api/tickets/{ticket_id}`

Used by:

- Workspace ticket detail

Expected detail domains:

- ticket core fields
- customer fields
- assignment fields
- market/channel fields
- conversation state
- OpenClaw conversation
- OpenClaw transcript
- OpenClaw attachment references
- active market bulletins
- system attachments

Frontend constraints:

- high-value contract; do not change field names without compatibility wrapper
- route refactor must keep selected-ticket fetching behavior intact

### POST `/api/lite/cases/{ticket_id}/workflow-update`

Used by:

- Workspace action panel

Payload concepts:

- status
- assignee_id
- required_action
- missing_fields
- customer_update
- resolution_summary
- human_note

Frontend constraints:

- dirty form protection must prevent accidental overwrite
- successful mutation invalidates queue and selected ticket detail

### POST `/api/lite/cases/{ticket_id}/ai-intake`

Used by:

- Workspace AI intake save

Payload concepts:

- ai_summary
- case_type
- suggested_required_action
- missing_fields
- last_customer_message

Frontend constraints:

- AI summary remains internal/agent-facing unless explicitly used for reply generation through reviewed flow

## WebChat Public Visitor API

### POST `/api/webchat/init`

Used by:

- public embedded widget

Payload concepts:

- tenant_key
- channel_key
- conversation_id
- origin
- page_url

Headers:

- optional `X-Webchat-Visitor-Token`

Expected behavior:

- creates or resumes visitor conversation
- returns safe visitor conversation identifier and visitor token

Frontend/widget constraints:

- no admin bearer token
- must not expose internal numeric ticket id
- must support old one-line snippet contract

### POST `/api/webchat/conversations/{conversation_id}/messages`

Used by:

- public embedded widget sending visitor messages

Headers:

- `X-Webchat-Visitor-Token`

Payload concepts:

- body

Expected behavior:

- validates visitor token
- validates message length/content
- creates visitor message

Frontend/widget constraints:

- send button should disable during pending send
- failures should show user-safe message

### GET `/api/webchat/conversations/{conversation_id}/messages`

Used by:

- public embedded widget polling/fetching visitor conversation

Headers:

- `X-Webchat-Visitor-Token`

Expected behavior:

- returns safe public message list
- no internal ticket ids
- no admin-only metadata

Frontend/widget constraints:

- current polling can remain fallback after realtime is added

## WebChat Admin API

### GET `/api/webchat/admin/conversations`

Used by:

- WebChat admin inbox

Expected behavior:

- requires admin bearer token
- returns conversations ordered by recent activity

Frontend constraints:

- can be updated by polling or realtime event invalidation
- should preserve selected conversation state where possible

### GET `/api/webchat/admin/tickets/{ticket_id}/thread`

Used by:

- WebChat admin thread view

Expected behavior:

- requires admin bearer token
- returns WebChat thread for an internal ticket id

Frontend constraints:

- admin-only; never called from visitor widget
- thread polling can remain fallback

### POST `/api/webchat/admin/tickets/{ticket_id}/reply`

Used by:

- WebChat admin manual reply

Payload concepts:

- body
- has_fact_evidence
- confirm_review

Expected behavior:

- requires admin bearer token
- passes through outbound safety gate
- writes WebChat reply / ticket comment / outbound message semantic record

Frontend constraints:

- safety errors must be shown clearly
- factual confirmation flags must remain explicit
- WebChat local ACK must remain distinct from external provider dispatch

## AI Config API

### GET `/api/admin/ai-configs`

Used by:

- AI Control / AI Governance Studio

Query concepts:

- config_type

Expected behavior:

- requires admin permission
- returns editable AI config resources

### POST `/api/admin/ai-configs`

Used by:

- create AI config resource

### PATCH `/api/admin/ai-configs/{resource_id}`

Used by:

- update draft AI config resource

### POST `/api/admin/ai-configs/{resource_id}/publish`

Used by:

- publish current draft into versioned config

### GET `/api/admin/ai-configs/{resource_id}/versions`

Used by:

- version history / rollback center

### POST `/api/admin/ai-configs/{resource_id}/rollback/{version}`

Used by:

- rollback to earlier snapshot by creating new published version

### GET `/api/lookups/ai-configs`

Used by:

- published config preview / runtime consumers

Frontend constraints:

- draft and published states must stay visually distinct
- invalid draft/schema should be blocked client-side before publish where schema exists
- server-side permission remains authoritative

## Channel / Accounts API

### GET `/api/admin/channel-accounts`

Used by:

- channel account management

### POST `/api/admin/channel-accounts`

Used by:

- create channel account

### PATCH `/api/admin/channel-accounts/{account_id}`

Used by:

- update channel account

Frontend constraints:

- no secrets should be displayed back into UI unless explicitly designed as masked/rotatable fields
- channel configuration and WebChat channel configuration should remain conceptually separated until unified by a reviewed data model

## Runtime / OpenClaw API

### GET `/api/admin/openclaw/runtime-health`

Used by:

- AppShell runtime badge for permitted users
- Runtime page

Expected behavior:

- requires permission
- returns health/warning state

### GET `/api/admin/openclaw/connectivity-check`

Used by:

- runtime diagnostics

### GET `/api/admin/production-readiness`

Used by:

- production readiness page/control plane

### GET `/api/admin/signoff-checklist`

Used by:

- readiness/signoff display

### GET `/api/admin/jobs?limit=50`

Used by:

- runtime job view

### POST `/api/admin/openclaw/events/consume-once`

Used by:

- manual consume/check action

### GET `/api/admin/openclaw/unresolved-events`

Used by:

- unresolved event management

### POST `/api/admin/openclaw/unresolved-events/{event_id}/replay`

Used by:

- replay unresolved event

### POST `/api/admin/openclaw/unresolved-events/{event_id}/drop`

Used by:

- drop unresolved event

Frontend constraints:

- dangerous actions require confirmation
- state must distinguish healthy, degraded, failing, and unauthorized
- runtime data must not be exposed to public visitors

## Future Realtime API

### GET `/api/events/stream`

Status:

- proposed target, not assumed present yet

Expected behavior:

- authenticated SSE stream
- permission-filtered events
- typed event payloads
- event ids for dedupe

Frontend constraints:

- keep fallback polling
- do not remove existing polling until realtime stability is proven
- do not expose admin event stream to visitor widget

## Contract review checklist

```text
[ ] Existing auth endpoints preserved
[ ] Existing ticket endpoints preserved
[ ] Existing WebChat visitor endpoints preserved
[ ] Existing WebChat admin endpoints preserved
[ ] Existing AI config endpoints preserved
[ ] Runtime/OpenClaw endpoints preserved
[ ] Public/admin boundaries preserved
[ ] 401 behavior preserved
[ ] Visitor token behavior preserved
[ ] No internal ticket id exposed in public API
```
