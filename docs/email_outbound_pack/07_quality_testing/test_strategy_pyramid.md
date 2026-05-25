# Test Strategy Pyramid

## Unit tests

- Email address normalization/validation.
- Header injection detection.
- Subject generation.
- HTML sanitization.
- Route/account resolver.
- Suppression checks.
- Provider response mapping.

## Integration tests

- `/outbound/send` with email capability ready.
- Worker dispatch with fake SES provider.
- Delivery event webhook idempotency.
- Inbound parser ticket linking.

## Contract tests

- Capability response shape.
- Email send request validation.
- Provider abstraction interface.

## Manual smoke

- Staging send to controlled mailbox.
- Delivery event appears.
- Bounce event appears using test mailbox/provider simulator.
- Inbound reply links to ticket.
