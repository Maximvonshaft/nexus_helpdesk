# ExternalChannel Unresolved Persistence Authority Design

## Status

- Work Item: #654
- Parent retirement: #572
- Baseline: `main@528309deed01f246568867e69cdbd235026cfc61`
- Delivery: runtime authority convergence and deletion of obsolete patch modules

## Problem

The service package imported `external_channel_unresolved_store` and applied a monkey patch at package import. The replacement function did not preserve the public Bridge signature: Bridge callers pass `event` and `error`, while the patched function expected decomposed fields. This created a latent runtime `TypeError` boundary and split persistence authority across three modules.

The ORM already natively owns:

- the `payload_hash` column;
- canonical-hash default behavior;
- the active-row unique index.

Dynamic ORM mapping and import-time replacement are therefore obsolete.

## Decision

`external_channel_bridge.persist_unresolved_external_channel_event` is the single persistence authority.

It must:

1. preserve the existing keyword-only public contract;
2. canonicalize payload JSON with stable key ordering and compact separators;
3. calculate SHA-256 directly;
4. reuse active duplicates by source, normalized session key and payload hash;
5. update only bounded route/error metadata on duplicate rows;
6. create new rows inside a nested transaction;
7. recover unique-index races by re-reading the active winner;
8. never commit or roll back the caller-owned transaction.

## Removed surfaces

- service-package ExternalChannel monkey patch;
- `external_channel_unresolved_store.py`;
- `external_channel_payload_hash.py`.

## Protected surfaces

This change does not remove or rename:

- `ExternalChannelUnresolvedEvent`;
- its table, columns or indexes;
- Alembic history;
- historical unresolved rows;
- the remaining Bridge compatibility functions.

## Security and privacy

No payload is newly logged or exported. Hashes are used for idempotency, not anonymization. The change introduces no network or external effect. Full payload storage behavior is unchanged and remains within the existing database boundary.

## Rollback

Revert the delivery merge. No database downgrade, row repair, Provider cleanup, customer communication or external-resource cleanup is required.
