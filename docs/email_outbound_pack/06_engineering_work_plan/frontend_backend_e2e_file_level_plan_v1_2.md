# Frontend + Backend E2E File-Level Plan v1.2

## Backend files

| Area | File | Required change |
|---|---|---|
| Settings | `backend/app/settings.py` | Add Email envs, fail-closed defaults, provider validation. |
| Models | `backend/app/models.py` | Add EmailChannelAccount, EmailOutboundMetadata, EmailDeliveryEvent, EmailInboundMessage, EmailSuppressionEntry. |
| Migration | `backend/alembic/versions/*email_outbound*.py` | Create Email tables and indexes. |
| Schemas | `backend/app/schemas.py` | Add Email account admin schemas, Email send fields, delivery event reads. |
| Resolver | `backend/app/services/channel_account_registry.py` | Provider-scoped resolver for all channel accounts. |
| Capability | `backend/app/services/outbound_channel_registry.py` | Email conditionally sendable based on runtime/account/recipient/suppression. |
| Ticket send | `backend/app/services/ticket_service.py` | Create Email metadata in same transaction as outbox row. |
| Dispatch | `backend/app/services/message_dispatch.py` | Channel-aware claim and Email branch. |
| Adapter | `backend/app/services/outbound_adapters/email.py` | Resolve route and dispatch via provider abstraction. |
| Provider | `backend/app/services/email_providers/ses.py` | SES API send + health/verification helpers. |
| Events | `backend/app/services/email_events.py` | Delivery/bounce/complaint ingestion and suppression. |
| Inbound | `backend/app/services/email_inbound.py` | Deterministic ticket linking. |
| Admin API | `backend/app/api/admin_email.py` | Account list/create/update/check/health/test-send/events/suppression. |
| Integration API | `backend/app/api/email_integrations.py` | SES/SNS event and inbound endpoints. |
| Router | `backend/app/main.py` | Register admin_email and email_integrations routers. |

## Frontend files

| Area | File | Required change |
|---|---|---|
| Types | `webapp/src/lib/types.ts` | Add EmailChannelAccount, EmailReadiness, EmailTestSendResult, EmailDeliveryEvent, EmailSuppressionEntry. |
| API | `webapp/src/lib/api.ts` | Add Email admin APIs and extended outbound send payload. |
| Admin UI | `webapp/src/routes/accounts.tsx` or `webapp/src/routes/email-accounts.tsx` | Add Email account configuration UI. |
| Agent UI | `webapp/src/components/operator/CustomerReplyPanel.tsx` | Add Email compose fields when channel=email. |
| Timeline | ticket timeline component | Render Email delivery/inbound/suppression events. |
| Copy | `webapp/src/lib/uxCopy.ts` | Add Email readiness and error labels. |
| Access | `webapp/src/lib/access.ts` | Add or reuse manage channel/email account permission. |

## Merge blockers

- Admin Email account UI missing: block merge.
- Agent Email compose missing: block merge.
- Backend-only Email send: block merge.
- Test send missing: block merge.
- Email queue/event breakdown missing: block merge.

## PR breakdown

1. Backend provider-scoped resolver + Email models/settings/migration.
2. Admin Email APIs + account readiness + test-send.
3. Email adapter + SES provider + worker dispatch.
4. Delivery events + inbound parser + suppression.
5. Frontend admin Email account UI.
6. Frontend agent Email composer + timeline events.
7. E2E smoke + docs + release evidence.
