# Governed TicketEvent Persistence Design

## Authority

- Work Item: #566
- Parent audit portfolio: #545
- Starting main: `e761bcfd9850451bcba7ffe679c4b83f87ec91bf`
- Contract: `nexus.ticket_event.writer.v1`

This design treats the accepted outcome and acceptance criteria in #566 as the product specification. It applies the Superpowers workflow by making the persistence boundary explicit before implementation, requiring RED/GREEN test evidence, and separating specification compliance from code-quality review.

## Problem

`TicketEvent` is an audit timeline record, but production callers currently instantiate the ORM model directly. Direct construction allows each caller to choose serialization, labels, free text, identifier handling and payload size independently. The Customer-visible path already has a recursive sanitizer, but that does not govern Tracking, Tool, Provider, Dispatch, escalation, background or internal-audit events.

The result is a durable-data boundary that is structurally inconsistent and cannot prove that prohibited PII, credentials, Provider payloads or Tool arguments/results never reach storage.

## Goals

1. Make one writer the only production path that creates a `TicketEvent`.
2. Require an explicit event class for every write.
3. Apply a versioned, fail-closed payload policy for each event class.
4. Preserve only approved operational join identifiers.
5. Bound field labels, notes, payload depth, cardinality, strings and final serialized bytes.
6. Detect direct ORM construction in CI.
7. Provide deterministic, scope-bound, idempotent historical scan/repair tooling that defaults to dry-run.
8. Preserve existing transaction ownership: the writer adds and flushes but never commits.

## Non-goals

- No new table, column, index, enum migration or Alembic revision.
- No production repair execution.
- No deployment, Provider enablement, customer outbound or external action.
- No attempt to turn `TicketEvent` into a queue, retry store, dispatch truth source or business-result authority.
- No first-class Tenant model work owned by #546.

## Event classes

The writer exposes six explicit classes:

| Class | Intended evidence | Payload posture |
|---|---|---|
| `customer_visible` | governed customer-visible message lifecycle | customer/output identifiers and bounded status labels only |
| `tracking` | approved Tracking source/result evidence | source/result IDs, bounded status/reason labels; never raw tracking values |
| `tool` | governed Tool decision/execution evidence | ToolCallLog ID, bounded tool/status/error labels; never arguments/results |
| `provider` | AI/communication Provider routing and result evidence | bounded provider/status/trace identifiers; never raw Provider payloads |
| `dispatch` | Operations Dispatch enqueue/attempt/result evidence | outbox/routing IDs and bounded state/reason labels |
| `internal_audit` | ticket lifecycle, escalation, handoff and internal control evidence | approved internal join IDs and bounded labels |

The existing coarse `EventType` enum remains unchanged. `event_class` is a writer policy input, not a new persisted column. The writer injects bounded metadata into `payload_json`:

```json
{
  "event_contract": "nexus.ticket_event.writer.v1",
  "event_class": "dispatch",
  "schema_version": 1
}
```

Unknown event classes, invalid `EventType` values, unsupported objects, cycles and serialization errors fail closed.

## Writer interface

```python
class TicketEventClass(str, Enum):
    CUSTOMER_VISIBLE = "customer_visible"
    TRACKING = "tracking"
    TOOL = "tool"
    PROVIDER = "provider"
    DISPATCH = "dispatch"
    INTERNAL_AUDIT = "internal_audit"

class TicketEventWriter:
    @classmethod
    def build(
        cls,
        *,
        ticket_id: int,
        event_type: EventType,
        event_class: TicketEventClass,
        actor_id: int | None = None,
        field_name: str | None = None,
        old_value: str | None = None,
        new_value: str | None = None,
        note: str | None = None,
        payload: Mapping[str, Any] | None = None,
        created_at: datetime | None = None,
    ) -> TicketEvent: ...

    @classmethod
    def add(cls, db: Session, **kwargs: Any) -> TicketEvent:
        row = cls.build(**kwargs)
        db.add(row)
        db.flush()
        return row
```

`build` supports callers and tests that need an ORM row without transaction side effects. `add` is the normal production API. Neither method commits.

## Payload and text policy

The existing recursive audit sanitizer remains the low-level untrusted-object defense. `ticket_event_sanitizer.py` is extended to accept a policy containing:

- allowed identifier keys;
- allowed bounded label keys;
- recursive limits;
- final byte limit;
- event class and schema version.

Policy behavior:

1. Start from an empty mapping for non-mapping input.
2. Recursively sanitize the supplied value.
3. Restore only policy-allowed identifiers that match the existing safe identifier grammar.
4. Preserve only policy-allowed low-cardinality labels after `safe_audit_label` normalization.
5. Inject contract metadata server-side; caller values cannot override it.
6. Serialize canonically with sorted keys and compact separators.
7. Collapse invalid or oversized content to a bounded marker containing contract metadata, safe identifiers and a SHA-256 prefix when available.

Free-text ORM fields are independently bounded:

- `field_name`: 120 characters, safe label grammar;
- `old_value` / `new_value`: 500 characters after sensitive-value redaction;
- `note`: 1,000 characters after sensitive-value redaction.

The writer must never persist raw tracking numbers, phone/email/address data, credentials, authorization headers, Provider group IDs, Provider payloads, Tool arguments or Tool results.

## Caller migration

Every production `TicketEvent(...)` call under `backend/app/` moves to `TicketEventWriter.add(...)`. Query-only imports of `TicketEvent` remain legal. Each caller supplies an explicit class based on the evidence it owns; no filename, event-name or payload heuristic chooses the class at runtime.

`audit_service.log_event` becomes a compatibility facade over the writer and requires or safely defaults an explicit class appropriate for ticket lifecycle/internal audit. New code should call the writer directly.

An AST architecture test scans Python files under `backend/app`, excluding:

- `models.py`, where the ORM class is declared;
- `services/ticket_event_writer.py`, where construction is authorized.

Any other `TicketEvent(...)` call fails CI with file and line evidence. Text search alone is insufficient because aliases and formatting can evade it; the AST rule also rejects aliases imported from `app.models` when called.

## Historical scan and repair

`backend/scripts/repair_ticket_events.py` is an offline administrative tool. It never runs during application startup or migration.

Safety contract:

- `--tenant-id` is mandatory.
- The script resolves the requested Tenant to server-owned ticket scope through the repository's current scope resolver. Until #546 establishes first-class Tenant ownership, only configured mappings are accepted; missing or ambiguous scope fails closed.
- Events are selected by joining `TicketEvent.ticket_id` to authorized Ticket IDs before payloads are read.
- Batches are ordered by event ID and bounded by `--batch-size`.
- Dry-run is the default. `--apply` is required to update rows.
- The scanner infers a repair class only from a versioned exact `EventType` mapping. Unmapped types are reported as `unclassified` and never mutated.
- A row changes only when canonical governed output differs from stored text.
- Re-running after apply produces zero changes.
- Output is a bounded JSON summary containing scope identifier, counts, event-class counts, reason counts, first/last ID and a digest. It never prints payloads or customer identifiers.
- The tool flushes and commits each bounded apply batch; any batch failure rolls back that batch and exits non-zero.

Because #546 is still open, the first delivery may implement the deterministic scanner/repair engine and a resolver interface with tests, while the production CLI rejects scopes that cannot be authoritatively resolved. It must not fall back to `default` or global scanning.

## Testing strategy

### RED evidence

Before production implementation, add tests that fail because:

- `TicketEventWriter` and event-class policies do not exist;
- direct `TicketEvent(...)` calls remain in production files;
- repair planning APIs do not exist.

Run the dedicated GitHub Actions gate and retain the failing exact-head evidence in the PR.

### GREEN evidence

Focused tests cover:

- all six classes and contract metadata;
- class-specific safe identifiers/labels;
- hostile nested, cyclic, Unicode, unsupported and oversized input;
- free-text redaction and truncation;
- writer transaction behavior (`add` + `flush`, no commit);
- customer-visible regression compatibility;
- representative Tool, Provider and Dispatch caller integrations;
- AST direct-construction prohibition;
- deterministic dry-run/apply/re-run repair behavior;
- missing/ambiguous Tenant scope fail-closed behavior;
- bounded redacted repair summaries.

The dedicated workflow compiles changed modules, runs focused tests, runs the existing customer-visible safety regression and checks exact-head whitespace.

## Rollout and rollback

The PR is code-only and remains Draft until exact-head CI and independent review settle. Merge changes only the construction path; no data is repaired automatically.

Rollback is a normal revert of the PR. Existing rows remain readable because the table schema and `EventType` enum do not change. Reverting does not replay or duplicate any Provider, Tool, Dispatch or customer-visible action. If historical repair is separately authorized later, its evidence and rollback plan must be reviewed independently before execution.

## Design self-review

- Placeholder scan: no TBD/TODO or unspecified implementation branch.
- Consistency: writer, sanitizer, architecture gate and repair engine share `nexus.ticket_event.writer.v1`.
- Scope: one persistence contract and one rollback boundary; no Tenant-model or deployment work is absorbed.
- Ambiguity: explicit classes are caller-owned; unknown class/type and unresolved Tenant scope fail closed.