# v1.2 End-to-End Business Value Gap Audit

## Final answer to the business question

v1.1 is not a complete end-to-end business-value closure. It is a backend-first production implementation package with frontend items listed, but it does not sufficiently specify the admin configuration workflow required for non-engineers to operate Email in production.

v1.2 closes that gap.

## Current main facts driving the gap

1. `webapp/src/routes/accounts.tsx` currently exposes provider options for WhatsApp, Telegram, and SMS only. Email is not available in the account management UI.
2. `webapp/src/components/operator/CustomerReplyPanel.tsx` currently sends `{ channel, body }` and has no Email-specific subject, to/from, cc/bcc, or verification UX.
3. `webapp/src/lib/api.ts` currently models `OutboundSendPayload` as `{ channel: string; body: string }` and must be extended without breaking existing channels.
4. Backend `ChannelAccount` exists but cannot represent full Email account governance by itself.
5. Backend admin `ChannelAccount` provider validation is tied to OpenClaw-style providers and must not be blindly extended.
6. Email channel is currently blocked as experimental until account governance, schema, and provider adapter exist.

## Business-value closure definition

Email outbound is only business-closed when all four loops are complete:

1. Configuration loop: Admin configures Email account, verifies readiness, runs test send.
2. Agent loop: Agent sees Email only when ready, composes subject/body, sends customer response.
3. Delivery loop: Worker sends through SES, provider message id is stored, delivery/bounce/complaint events are visible.
4. Reply loop: Customer replies by Email and the message links back to the correct ticket or enters unresolved review.

## v1.2 decision

The implementation must include both backend and frontend changes. A backend-only implementation is not accepted as production-ready.
