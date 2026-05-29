# Email Thread Identity Template Parity Evidence

## Scope

This PR closes one Email v1.7.8 template gap without overlapping the open internal-note and delivery-evidence PRs:

- The ticket summary API now returns `email_thread`.
- `/email` shows a first-class mailbox/thread identity block inside the selected Email thread.
- The block is derived from existing ticket, customer, and outbound-message state. It does not claim true inbound mailbox sync.

## Real Data Sources

- Recipient: `Ticket.preferred_reply_contact` or `Customer.email`.
- Inbound source: `Ticket.source_chat_id`.
- Thread identity: latest `TicketOutboundMessage.provider_message_id`, falling back to `Ticket.source_chat_id`.
- Latest outbound status: latest `TicketOutboundMessage.status`.
- Latest provider status: latest `TicketOutboundMessage.provider_status`.

## Local Verification

- `node --test tests/email-workbench-contract.test.mjs`: 5 passed.
- `npm run typecheck`: passed.
- `npm run lint`: passed with 5 existing warnings.
- `npm test`: 67 passed.
- `npm run build`: passed; existing `vendor-livekit` chunk warning remains.
- `git diff --check`: passed.
- `python -m py_compile backend\app\api\ticket_perf.py backend\app\api\tickets.py backend\app\services\ticket_service.py`: passed.
- `python -m pytest -q backend\tests\test_channel_workbench_backend_contracts.py -p no:cacheprovider --tb=short --basetemp=C:\tmp\nexus_pytest_email_thread_identity`: 2 passed.
- `python -m pytest -q backend\tests\test_channel_workbench_backend_contracts.py backend\tests\test_ticket_detail_summary.py backend\tests\test_ticket_lightweight_contract.py -p no:cacheprovider --tb=short --basetemp=C:\tmp\nexus_pytest_email_thread_identity_full`: 5 passed.
- In-app browser smoke: `http://127.0.0.1:5174/email` redirected to `/login` unauthenticated, no Vite error overlay and no severe console errors.
