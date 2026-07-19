# ExternalChannel retirement boundary

## Current status

ExternalChannel is not a supported transport authority. Production configuration must keep every ExternalChannel execution switch disabled. The repository may retain historical persistence names and bounded compatibility reads only where they are required to interpret existing data.

The former independent inventory/workflow authority is retired. One parent compatibility lifecycle now owns policy; the retained detailed registry is only its fail-closed discovery subroutine. Current repository governance is owned by:

- `README.md` — product and runtime authority;
- `config/architecture/service-authority.v1.json` — backend public/core/shim ownership;
- `config/architecture/compatibility-lifecycle.v1.json` — sole policy, owner, replacement and deadline authority;
- `config/governance/legacy-surface-domains.v1.json` — subordinate marker discovery only;
- `scripts/verify_repository.py` plus focused backend tests — local verification authority;
- Alembic — the only executable schema-mutation authority.

## Fail-closed configuration

The following values remain mandatory until the compatibility surface is removed:

```env
EXTERNAL_CHANNEL_TRANSPORT=disabled
EXTERNAL_CHANNEL_DEPLOYMENT_MODE=disabled
EXTERNAL_CHANNEL_SYNC_ENABLED=false
EXTERNAL_CHANNEL_INBOUND_AUTO_SYNC_ENABLED=false
EXTERNAL_CHANNEL_EVENT_DRIVER_ENABLED=false
EXTERNAL_CHANNEL_BRIDGE_ENABLED=false
EXTERNAL_CHANNEL_CLI_FALLBACK_ENABLED=false
```

No document, test, local script, compose overlay or compatibility model may authorize enabling those values.

## Allowed residuals

A residual is allowed only when all of the following are true:

1. it is needed to read historical rows, preserve migration compatibility or complete an explicitly owned retirement step;
2. it does not create a second customer-message transport;
3. it cannot be enabled by a default, alias or undocumented environment variable;
4. it has an owner and, when temporary, a removal deadline in the compatibility lifecycle manifest;
5. it does not contain secrets, live credentials, customer payloads or production endpoints.

Historical model/table/enum names are not proof of an active transport. Conversely, disabled configuration is not proof that callers or writes are absent; destructive removal requires runtime and data evidence.

## Stop-new-writes completion

Current application routes and workers no longer create ExternalChannel links, sync cursors, unresolved events, attachment persistence records or legacy background jobs. Historical GET surfaces remain bounded and read-only. Any reintroduction of mutation routes, worker job types or provider calls fails canonical verification.

## Removal sequence

1. Enumerate current callers and write paths from the exact candidate revision.
2. Prove that all customer-visible messaging is owned by the canonical Provider/channel boundaries.
3. Stop new legacy writes and fail closed on attempted use.
4. Observe a defined period and investigate every non-zero read/write/call.
5. Preserve historical-read and rollback evidence.
6. Remove safe code and configuration.
7. Rehearse any destructive schema/data change through backup, restore, reconciliation, Alembic upgrade/downgrade and retention review.
8. Execute destructive migration only under a separately approved release candidate.

Code search alone does not prove zero production traffic. Re-enabling ExternalChannel is not an acceptable rollback strategy.

## WhatsApp and Provider non-regression boundary

ExternalChannel retirement must not alter the canonical channel architecture:

- customer-visible messages remain governed;
- Provider routing and traffic selection remain configuration-driven;
- the WhatsApp connector owns only its socket/channel lifecycle;
- tests and migration tools cannot send real outbound traffic;
- repository verification does not enable a Provider or mutate production.

## Evidence requirements

A removal PR must attach bounded evidence containing identifiers, counts and hashes rather than payloads. Required evidence includes caller inventory, traffic/write observations, historical-read validation, backup/restore rehearsal when schema is affected, rollback procedure and the exact source/tree identity that was verified.
