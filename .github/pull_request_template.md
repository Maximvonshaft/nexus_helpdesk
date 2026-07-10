# Work Item

- Closes: #
- Parent Epic: #
- Governance index: #489

## Exact implementation facts

- Base branch: `main`
- Base SHA: `REPLACE_ME`
- Head SHA: `REPLACE_ME`
- Current implementation PR for this Work Item: Yes / No

## Outcome

Describe the observable result delivered by this PR. Do not copy only the Issue title.

## Scope

### Changed

- 

### Explicitly unchanged

- Customer-visible message boundary
- Governed tool execution boundary
- Tenant/country/channel isolation
- Production deployment and real outbound

## Safety and architecture checks

- [ ] No C-end long-term customer memory was introduced.
- [ ] MCP/approved operational sources remain authoritative for live facts.
- [ ] Customer claims and previous AI replies are not treated as facts.
- [ ] Customer-visible output uses `CustomerVisibleMessageService` or the governed outbound contract.
- [ ] AI actions use the governed policy/execution boundary.
- [ ] Tenant, country, channel, permission, and privacy boundaries are preserved.
- [ ] Raw prompts, provider payloads, tool arguments/results, credentials, tracking, phone/email, addresses, and provider group IDs are not exposed on unsafe surfaces.
- [ ] This is the only current implementation PR for the linked Work Item.

## Data, migration, and compatibility

- Schema migration: None / Revision `...`
- Down revision: `...`
- Data backfill or repair: None / Describe
- Upgrade evidence: Not applicable / Describe
- Downgrade and re-upgrade evidence: Not applicable / Describe
- Backward compatibility: Describe

## Validation

| Gate | Command or workflow | Result | Evidence |
|---|---|---|---|
| Compile/static |  |  |  |
| Focused tests |  |  |  |
| Regression |  |  |  |
| PostgreSQL/migration |  |  |  |
| Concurrency/idempotency |  |  |  |
| Security/redaction |  |  |  |
| Frontend/accessibility |  |  |  |

## Runtime evidence

- Environment: None / SQLite / PostgreSQL / Staging / Production-like
- Runtime proof completed:
- Runtime proof not completed:
- Why remaining gaps are acceptable for this merge stage:

## Failure, recovery, and rollback

Describe partial-failure behavior, retry/idempotency, worker/process recovery, feature-flag behavior, and exact rollback steps.

## Not verified

List every material item not verified. Do not use green CI as a substitute for runtime proof.

## Release declaration

- [ ] The linked Work Item acceptance criteria are satisfied for this merge stage.
- [ ] Exact-head checks were reviewed after the final commit.
- [ ] No unresolved blocking review thread remains.
- [ ] No production deploy, tag, real customer outbound, funds/legal/identity action, or irreversible deletion is included unless separately authorized.
- [ ] The PR remains Draft until the release owner accepts exact-head evidence.
