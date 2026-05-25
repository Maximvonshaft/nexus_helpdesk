# File-level Change Plan

## Change Table

| Change ID | File Path | Component/Function | Current Problem | Required Change | Tests | Risk |
|---|---|---|---|---|---|---|
| FC-001 | `backend/app/settings.py` | Settings | No email provider settings | Add fail-closed email settings | T-001 | R3 |
| FC-002 | `backend/app/models.py` | Data model | No email account/metadata/event/inbound/suppression tables | Add models | T-002 | R4 |
| FC-003 | `backend/alembic/versions/*.py` | Migration | No email schema | Add migration | T-002 | R4 |
| FC-004 | `backend/app/schemas.py` | OutboundSendRequest | Body-only schema insufficient | Add optional email fields and output models | T-003 | R3 |
| FC-005 | `backend/app/services/outbound_channel_registry.py` | Capability registry | Email hard-blocked | Make Email conditionally sendable | T-004 | R4 |
| FC-006 | `backend/app/services/outbound_adapters/email.py` | New adapter | Missing provider adapter | Implement route resolve + dispatch | T-005 | R4 |
| FC-007 | `backend/app/services/email_providers/base.py` | Provider abstraction | Missing | Add base contract | T-005 | R3 |
| FC-008 | `backend/app/services/email_providers/ses.py` | SES provider | Missing | Implement SES send | T-006 | R4 |
| FC-009 | `backend/app/services/message_dispatch.py` | Dispatch path | Email not handled as dedicated adapter | Add email branch while preserving existing channels | T-005,T-006 | R4 |
| FC-010 | `backend/app/services/ticket_service.py` | Send creation | Email metadata not created | Validate/create EmailOutboundMetadata | T-003,T-005 | R4 |
| FC-011 | `backend/app/services/email_events.py` | Delivery events | Missing | Parse/upsert delivery/bounce/complaint | T-007 | R4 |
| FC-012 | `backend/app/services/email_inbound.py` | Inbound reply | Missing | Parse/link inbound replies | T-008 | R4 |
| FC-013 | `backend/app/api/email_integrations.py` | Webhooks | Missing | Add event/inbound endpoints | T-007,T-008 | R4 |
| FC-014 | `backend/app/main.py` or router registration | API registration | Email integration router absent | Register router | T-009 | R3 |
| FC-015 | `backend/app/services/email_security.py` | Security validation | Missing | Header injection, sanitize, suppression checks | T-010 | R4 |
| FC-016 | `backend/tests/test_email_channel_capabilities.py` | Tests | Missing | Add capability tests | T-004 | R3 |
| FC-017 | `backend/tests/test_email_outbound_adapter.py` | Tests | Missing | Add route/provider/dispatch tests | T-005,T-006 | R4 |
| FC-018 | `backend/tests/test_email_delivery_events.py` | Tests | Missing | Add webhook/event tests | T-007 | R4 |
| FC-019 | `backend/tests/test_email_inbound_parser.py` | Tests | Missing | Add inbound matching tests | T-008 | R4 |
| FC-020 | `backend/tests/test_email_security.py` | Tests | Missing | Add injection/suppression/secret tests | T-010 | R4 |
| FC-021 | `webapp/src/lib/types.ts` | Frontend types | Email fields/capability not modeled | Add email fields/types | T-011 | R2 |
| FC-022 | `webapp/src/lib/api.ts` | API client | Missing email payload support | Add typed request support | T-011 | R2 |
| FC-023 | `webapp/src/routes/...ticket...` | Reply UI | Email compose not available | Add capability-driven Email compose | T-011,T-012 | R3 |
| FC-024 | `webapp/src/routes/accounts.tsx` | Admin UI | No email account governance | Add or expose Email account config/health | T-012 | R3 |
| FC-025 | `docs/ops/EMAIL_OUTBOUND_RUNBOOK.md` | Runbook | Missing | Add setup/smoke/rollback SOP | T-013 | R2 |
| FC-026 | `deploy/.env.prod.example` | Env template | Missing email flags | Add fail-closed email envs | T-001 | R3 |

## Required implementation notes

### FC-001 Settings

Add:
- `OUTBOUND_EMAIL_ENABLED=false`
- `EMAIL_PROVIDER=disabled`
- `EMAIL_PROVIDER_REGION`
- `EMAIL_SES_CONFIGURATION_SET`
- `EMAIL_WEBHOOK_SECRET`
- `EMAIL_INBOUND_ENABLED=false`
- `EMAIL_DELIVERY_EVENTS_ENABLED=false`
- `EMAIL_DEFAULT_FROM_EMAIL`
- `EMAIL_DEFAULT_FROM_NAME`
- `EMAIL_MAX_RECIPIENTS=1`
- `EMAIL_ALLOW_CLICK_OPEN_TRACKING=false`

Validate allowed values:
- `EMAIL_PROVIDER in {"disabled","ses"}` for V1.
- Production must not allow `OUTBOUND_EMAIL_ENABLED=true` when `EMAIL_PROVIDER=disabled`.

### FC-005 Capability

Email ready only if:
- `ENABLE_OUTBOUND_DISPATCH=true`
- `OUTBOUND_EMAIL_ENABLED=true`
- `EMAIL_PROVIDER=ses`
- active `ChannelAccount(provider="email")`
- linked verified `EmailChannelAccount`
- valid recipient
- recipient not suppressed

### FC-009 Dispatch

In `process_outbound_message`:
- Add explicit `if message.channel == SourceChannel.email`.
- Call `dispatch_email_outbound`.
- Do not route email through `dispatch_via_openclaw_bridge`.
- Preserve current non-email branch.

### FC-013 Webhooks

Add auth/signature guard.
Webhook must be idempotent.
Never trust provider payload without validation.

## v1.1 P0/P1 Change Addendum

The following changes are mandatory and override any ambiguous v1 wording.

| Guardrail ID | File Path | Required Change | Required Tests | Priority |
|---|---|---|---|---|
| P0-001 | `backend/app/services/channel_account_registry.py` or equivalent | Add provider-scoped account resolver and provider constants. Do not reuse OpenClaw-only constants for Email governance. | `test_provider_scoped_channel_account_resolution.py` | P0 |
| P0-002 | `backend/app/services/openclaw_bridge.py` | Stop using unscoped `resolve_channel_account(...)` for new generic routing. Either deprecate it or make call sites pass provider explicitly. | Non-email resolver never returns email account | P0 |
| P0-003 | `backend/app/api/admin.py` | Allow Email account governance through proper provider registry, not by extending OpenClaw-only constants blindly. | Create/update email ChannelAccount validates provider and companion config | P0 |
| P0-004 | `backend/app/services/message_dispatch.py` | Add channel-aware eligible-channel calculation for worker claims. Do not claim Email when disabled. | Email disabled worker smoke leaves pending Email untouched | P0 |
| P0-005 | `backend/app/services/message_dispatch.py` | Add Email-specific runtime gate. Keep `OUTBOUND_PROVIDER=openclaw`; add `OUTBOUND_EMAIL_ENABLED` and `EMAIL_PROVIDER=ses`. | `OUTBOUND_PROVIDER=ses` not needed and not accepted | P0 |
| P0-006 | `backend/app/services/message_dispatch.py` | If an already-claimed Email row is processed while Email disabled, reset to pending/paused with retry_count unchanged. | Processing Email + disabled does not dead-letter | P0 |
| P0-007 | `backend/app/schemas.py` | Extend `OutboundSendRequest` backward-compatibly with optional email fields. | Existing WhatsApp/WebChat payloads still pass | P0 |
| P0-008 | `backend/app/services/email_inbound.py` | Forbid subject-similarity auto-link in V1. Use deterministic matching only. | Subject-only match creates unresolved/manual review, not linked ticket | P0 |
| P0-009 | `backend/app/api/email_integrations.py` | Implement concrete webhook verification: SNS signature or HMAC timestamp anti-replay. | Unsigned/stale/fake payloads rejected | P0 |
| P1-001 | `backend/app/api/admin.py` / queue summary service | Add channel-specific Email queue and event counts. | Queue summary reports email-specific counts | P1 |
| P1-002 | `backend/app/services/email_events.py` | Bounce/complaint creates suppression and updates timeline/event rows. | Bounce/complaint suppression tests | P1 |
| P1-003 | `backend/app/services/email_security.py` | Header injection and HTML/body sanitization safeguards. | CRLF and HTML script tests | P1 |
| P1-004 | `docs/ops/EMAIL_PROVIDER_SETUP_SES.md` | Add SES inbound region, MX, DKIM, SPF, DMARC, event-route preflight. | Manual preflight evidence checklist | P1 |

## v1.1 implementation sequencing

Implement in this order:

1. Provider-scoped resolver and tests.
2. Email settings + channel-aware worker claim.
3. Email schema + capability readiness.
4. Email tables/migration.
5. Email adapter + SES provider.
6. Delivery events + suppression.
7. Inbound deterministic parser.
8. Frontend/admin UI.
9. Ops runbooks and final smoke evidence.

Do not start SES provider code before P0-001 through P0-007 are passing.


## v1.2 E2E Business Closure Addendum

The following frontend/backend items are mandatory for business closure. They override any wording that treats frontend/admin UI as optional.

| Guardrail ID | File Path | Required Change | Priority |
|---|---|---|---|
| E2E-ADMIN-001 | `backend/app/api/admin_email.py` | Add Email account list/create/update/check-verification/health-check/test-send APIs. | P0 |
| E2E-ADMIN-002 | `backend/app/schemas.py` | Add request/response schemas for Email admin configuration and readiness. | P0 |
| E2E-ADMIN-003 | `webapp/src/routes/accounts.tsx` or `webapp/src/routes/email-accounts.tsx` | Add backend/admin UI for Email account configuration. | P0 |
| E2E-ADMIN-004 | `webapp/src/lib/api.ts` | Add Email admin API client methods and extended Email outbound send payload. | P0 |
| E2E-ADMIN-005 | `webapp/src/lib/types.ts` | Add Email account/readiness/test-send/event/suppression types. | P0 |
| E2E-AGENT-001 | `webapp/src/components/operator/CustomerReplyPanel.tsx` | Render Email compose mode with From/To/Subject/Body/CC/BCC and disabled reasons. | P0 |
| E2E-AGENT-002 | timeline component | Render Email-specific delivery and inbound timeline cards. | P1 |
| E2E-OPS-001 | `backend/app/api/admin.py` or queue summary service | Add Email-specific queue/event counts. | P1 |

Do not accept a backend-only Email implementation. The feature is complete only when an admin can configure Email from the backend UI and an agent can use Email from the ticket UI.
