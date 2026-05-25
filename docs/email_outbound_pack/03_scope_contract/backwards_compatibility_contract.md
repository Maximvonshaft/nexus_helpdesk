# Backwards Compatibility Contract

## Must not break

- Existing WhatsApp outbound adapter behavior.
- Existing Telegram/SMS generic OpenClaw bridge behavior.
- Existing WebChat local-only semantics.
- Existing `TicketOutboundMessage` API fields.
- Existing auth/RBAC behavior.
- Existing outbox retry semantics.
- Existing deployment default fail-closed behavior.

## Additive-only rules

- Add optional request fields.
- Add new tables.
- Add new settings with safe defaults.
- Add new endpoints behind integration auth.
- Add new UI states without removing existing routes.

## Migration compatibility

- Migrations must be forward-only safe.
- New tables must not require backfilling existing rows.
- Downgrade may drop new tables only if project convention allows downgrade; otherwise document no-downgrade policy.
- Existing production data must not be mutated except creation of new metadata rows on new email sends.
