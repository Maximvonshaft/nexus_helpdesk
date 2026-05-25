# v1.2 End-to-End Business Value Contract

## Objective

Make Email a production-grade customer support channel that can be configured by an authorized admin in the backend/admin UI and used by support agents inside the ticket workspace.

## Must be delivered

### Backend

- Email account data model and migrations.
- Email-specific outbound metadata.
- SES provider abstraction.
- Email adapter integrated into worker dispatch.
- Provider-scoped channel account resolution.
- Email-specific runtime gates.
- Delivery event webhook verification and ingestion.
- Inbound reply parser and deterministic ticket linking.
- Email suppression handling.
- Admin APIs for account configuration, verification, health, test-send, event review, suppression review.
- Queue summary with Email-specific counts.

### Frontend

- Admin Email Account configuration UI.
- Admin verification/health/test-send UI.
- Agent Email reply composer.
- Email disabled-reason display.
- Email timeline event rendering.
- Queue/admin observability UI for Email backlog and failures.

### DevOps / Provider setup

- Env flags remain fail-closed.
- Secrets are not stored in DB.
- DNS/domain/SES verification remain provider/DevOps-controlled but surfaced in backend readiness UI.

## Not acceptable

- Backend-only SES send without admin UI.
- Email appearing as a selectable channel before it is ready.
- Raw SES credentials stored in the database.
- Email routed through OpenClaw.
- Subject similarity auto-linking inbound Email to tickets.
- Rollback that marks pending Email as dead.

## Business-ready acceptance gate

A reviewer must be able to perform this path:

1. Open admin backend.
2. Create Email channel account for a market.
3. See missing DNS/secret/provider readiness items.
4. Complete DevOps/provider prerequisites.
5. Refresh health/verification.
6. Run test send to an allowed test recipient.
7. Open a ticket with customer email.
8. Select Email and send a reply.
9. Confirm timeline shows queued and provider accepted.
10. Simulate delivery/bounce/complaint webhook.
11. Confirm delivery event and suppression behavior.
12. Simulate inbound reply.
13. Confirm reply attaches to the correct ticket or unresolved review.
