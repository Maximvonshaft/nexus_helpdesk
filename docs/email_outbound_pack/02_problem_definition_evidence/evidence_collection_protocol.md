# Evidence Collection Protocol

## Before implementation

Capture:
- Current channel capabilities response.
- Current Email unavailable response from `/outbound/send`.
- Current outbound registry behavior tests.
- Existing worker/outbox behavior.

## During implementation

For each PR capture:
- Diff summary.
- Test results.
- Migration upgrade/downgrade proof.
- Capability JSON before/after.
- Failure examples.

## Staging smoke evidence

Required files:
- `email_channel_capabilities_before.json`
- `email_channel_capabilities_after.json`
- `email_outbound_queue_before.json`
- `email_outbound_queue_after.json`
- `worker_email_send.log`
- `provider_send_result.json`
- `email_delivery_event_payload.json`
- `sample_ticket_timeline.json`
- `inbound_reply_linking_result.json`
- `rollback_command.txt`

## Production verification

Within first rollout window:
- Confirm no unexpected sends.
- Confirm no duplicate sends.
- Confirm bounce/complaint event endpoint is not producing 4xx/5xx.
- Confirm support agents can see disabled reasons and delivery states.
