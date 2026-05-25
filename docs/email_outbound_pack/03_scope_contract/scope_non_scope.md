# Scope / Non-scope

## In scope

### Backend
- Add Email channel capability readiness logic.
- Add email-specific runtime settings.
- Add `EmailChannelAccount` model and migration.
- Add `EmailOutboundMetadata` model and migration.
- Add `EmailDeliveryEvent` model and migration.
- Add `EmailInboundMessage` model and migration.
- Add `EmailSuppressionEntry` model and migration.
- Extend outbound send schema with optional email-specific fields.
- Implement `backend/app/services/outbound_adapters/email.py`.
- Implement provider abstraction and AWS SES provider.
- Integrate Email adapter into `message_dispatch.py`.
- Add delivery event webhook endpoint.
- Add inbound email webhook/parser/linker.
- Add tests.

### Frontend
- Surface Email only through capability API.
- Add Email compose fields when channel=email.
- Show disabled reasons.
- Show delivery/bounce/complaint timeline states if exposed by API.

### Ops
- Add SES setup runbook.
- Add staging/prod env matrix.
- Add smoke test script.
- Add rollback instructions.

## Non-scope

- Marketing/bulk email campaigns.
- Open/click tracking by default.
- Self-hosted SMTP server operation.
- Full attachment sending in initial V1 unless existing attachment pipeline can be safely reused.
- AI-generated email automation without human approval.
- Silent fallback to another customer's email.
- Sending to multiple primary recipients by default.
- Changing WhatsApp/Telegram/SMS behavior except shared registry improvements.
