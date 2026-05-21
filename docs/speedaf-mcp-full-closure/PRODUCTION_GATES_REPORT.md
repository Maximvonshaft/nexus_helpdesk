# Speedaf Production Gates Closure Report

## Scope

This PR is a backend quality-gate hardening pass after PR-A, PR-B, and PR-C.

Included:

1. Make Speedaf idempotency migrations reversible.
2. Move `order/updateAddress` execution out of the HTTP request path and into BackgroundJob processing.

Excluded:

- Operator UI wiring.
- Real Speedaf UAT.
- Production feature flag enablement.
- `callData/voice/callBack`.

## Migration Gate

The following migrations now have real downgrade behavior:

- `20260521_0027_speedaf_cancel_controlled_action.py`
- `20260521_0028_speedaf_address_update_idempotency.py`

Expected migration contract:

- `upgrade head` creates required idempotency tables and indexes.
- `downgrade -1` for 0028 drops address-update indexes/table.
- `downgrade -1` for 0027 drops cancel indexes/table.
- `upgrade head` remains reentrant-safe.

## Address Update Gate

Before this hardening pass, `/speedaf/address-update` reserved idempotency and synchronously called Speedaf.

After this hardening pass:

1. HTTP route validates feature flag, operator capability, ticket visibility, rate limit, and idempotency.
2. HTTP route queues `speedaf.address_update.submit` BackgroundJob.
3. HTTP response returns `queued`, not `submitted`.
4. Worker calls `SpeedafActionService.submit_update_address_flow()`.
5. Worker updates idempotency status and writes completion/failure TicketEvent.

This keeps slow or failing Speedaf calls out of the operator request path.

## Safety Boundary

- Write feature flags remain default-off.
- AI does not execute write actions directly.
- Address update response does not claim the address has already changed.
- Job payload is backend-internal; TicketEvent payloads remain redacted.
- Voice callback remains excluded.

## Focused Tests

Updated focused tests cover:

- work order disabled by default
- work order queues BackgroundJob
- work order description is limited to 200 characters
- address update disabled by default
- address update queues BackgroundJob without synchronous Speedaf call
- address update worker executes Speedaf action and writes completion event
- duplicate address update remains blocked by DB-backed idempotency

## Remaining Production Gates

- Real Speedaf UAT.
- Production appCode / whitelist / sign-rule validation.
- Operator UI wiring.
- Staging smoke before enabling write-action flags.
