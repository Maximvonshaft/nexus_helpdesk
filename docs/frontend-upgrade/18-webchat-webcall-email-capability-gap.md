# WebChat / WebCall / Email capability gap matrix

Date: 2026-05-28
Scope: current `main` backend contracts plus PR #279 draft changes. PR #279 must remain draft and must not be merged until CI is green and the remaining product gaps are accepted.

## Matrix

| Area | Real API connected | Frontend surface | Backend / audit / RBAC / timeline / draft-save status | Remaining gap |
| --- | --- | --- | --- | --- |
| WebChat inbox and handoff | Yes: `/api/webchat/admin/conversations`, `/api/webchat/admin/tickets/{ticket_id}/thread`, `/api/webchat/admin/tickets/{ticket_id}/reply`, handoff queue/accept/decline/force/release/resume APIs. | Existing `/webchat` operator inbox and WebChat inbox v5 components. | Backend owns WebChat messages/events and handoff audit. RBAC exists for handoff capabilities. Ticket timeline includes `webchat_event` rows through `/api/tickets/{ticket_id}/timeline`. | No gap for basic real API wiring. Delivery receipts and richer external-channel parity remain channel-specific work, not WebChat core. |
| WebCall human voice | Yes: `/api/webchat/admin/voice/sessions`, `/api/webchat/admin/tickets/{ticket_id}/voice/sessions`, `/accept`, `/reject`, `/end`, public `/api/webchat/conversations/{conversation_id}/voice/sessions`. | Existing operator `/webchat-voice` route and `AgentWebCallPanel`; public customer route remains `/webcall/$voice_session_id`. | Backend enforces `webcall.voice.*` capabilities, writes `WebchatMessage.message_type=voice_call`, emits WebChat voice events, and exposes voice evidence through ticket timeline. | The v1.7.8 top-level operator `/webcall` workbench is not retained in PR #279 because the WebCall scope guard is intentionally strict. Product should either expand the guard in a dedicated PR or keep `/webchat-voice` as the operator entry. |
| Email operator workbench | Yes: `/api/lite/cases`, `/api/tickets/{ticket_id}/summary`, `/api/tickets/{ticket_id}/timeline`, `/api/tickets/{ticket_id}/outbound/channels/capabilities`, `/api/tickets/{ticket_id}/outbound/templates`, `/api/tickets/{ticket_id}/outbound/draft`, `/api/tickets/{ticket_id}/outbound/send`. | PR #279 adds direct `/email` workbench and exposes it through AppShell navigation plus CommandPalette. It is a ticket-backed frontend workbench over the existing ticket queue, not an independent inbound Email backend. | `/email` uses unified `routeAccess` and requires `ticket.read` plus at least one of `outbound.draft.save` or `outbound.send`. Reply templates are generated server-side from ticket/customer context and only populate the draft form; they do not write timeline or outbound rows until the operator saves/sends. Draft save uses the shared frontend API client, writes `TicketOutboundMessage(status=draft, provider_status=draft_saved)` and `TicketEvent(outbound_draft_saved)`. Send writes queued outbound message and `TicketEvent(outbound_queued)`. Timeline returns both `outbound_message` and `ticket_event`. | Missing true inbound Email ingestion/sync, mailbox thread identity, delivery receipt UI, and attachment send support. The queue uses ticket metadata (`source_channel/category/sub_category`) as the Email candidate source. |
| SMTP account administration | Yes: `/api/admin/outbound-email/accounts`, enable/disable/update/test-send. | Existing `/outbound-email` admin page. | Admin audit log is redacted for account create/update/password/test-send. RBAC is `channel_account.manage`. | This is configuration, not the operator Email queue. It does not prove customer reply timeline unless paired with ticket outbound draft/send tests. |
| Today workspace / generic ticket reply | Yes: ticket summary, timeline, workflow update, AI intake, outbound channel capabilities, outbound send. | Existing `/workspace` and `/` routes. | Real ticket/outbound API and RBAC are already in place. | It is a generic ticket console, not an Email-specific queue. |

## Contract evidence added in PR #279

- `backend/tests/test_channel_workbench_backend_contracts.py` proves Email capability readiness, draft save, outbound send, ticket event audit, and timeline readback using real FastAPI routes and an isolated database.
- The same backend contract test proves WebCall ringing queue, accept, end, and ticket timeline voice evidence readback through real FastAPI routes.
- `webapp/tests/email-workbench-contract.test.mjs` locks the `/email` workbench `routeAccess` RBAC semantics, AppShell/CommandPalette entrypoints, tokenized Email queue filtering, server-backed reply templates, API-client draft save, outbound send call, and timeline invalidation loop.

## Explicit product decisions

- PR #279 keeps the top-level WebCall operator workbench out of scope. The WebCall PR Guard allowlist is only adjusted for the explicit Email workbench, contract, and documentation files already changed by this PR so shared shell/API edits do not fail as a false positive.
- `/email` draft save is implemented against the existing backend endpoint; it is not marked as a fake frontend-only save.
- Email queue filtering is intentionally conservative: tokenized `email`, `mail`, `smtp`, `imap`, `pop3` markers are accepted; arbitrary substring matches are not.
