# TicketEvent Persistence Contract

## Status and authority

- Work Item: #566
- Contract: `nexus.ticket_event.writer.v1`
- Classification contract: `nexus.ticket_event.classification.v1`
- Historical mapping: `nexus.ticket_event.repair.mapping.v2`
- Authoritative production creation boundary: `app.services.ticket_event_writer.TicketEventWriter`
- Database migration: none
- Automatic historical mutation: none

`TicketEvent` is a durable audit timeline. It is not a queue, retry store, worker lease, dispatch truth source, customer-memory store, Provider transcript store or Tool argument/result store.

## Required write path

Production code creates timeline rows only through an explicit writer or the generic audit facade, which resolves one trustworthy event class before calling the same writer:

```python
TicketEventWriter.add(
    db,
    ticket_id=ticket.id,
    event_type=EventType.field_updated,
    event_class=TicketEventClass.INTERNAL_AUDIT,
    note="bounded audit summary",
    payload={"ticket_id": ticket.id, "status": "updated"},
)
```

`build(...)` creates a governed ORM row without adding it to a session. `add(...)` validates, sanitizes, adds and flushes the row. Neither method commits or rolls back; the caller retains transaction ownership.

Only `payload=None` means that no caller payload was supplied and is converted to a valid empty mapping. Falsey non-mapping inputs such as `[]`, `""`, `0` and `False` remain invalid inputs and produce a bounded `event_payload_invalid` marker; they are never silently normalized to `{}` and their raw content is not persisted.

Unknown classes, non-`EventType` values, invalid identities, cycles, unsupported objects, unbounded content and serialization failures fail closed or collapse to a bounded redaction marker.

## Event classes

| Event class | Intended evidence | Approved examples |
|---|---|---|
| `customer_visible` | governed customer-visible message lifecycle | outbound/message/comment database IDs, bounded RFC Message-ID correlation, provider status, reply channel, external-send flag |
| `tracking` | approved Tracking source/result evidence | audit ID, tracking-number hash, source/status/tool labels; never a raw tracking number |
| `tool` | PolicyGate/ControlledAction/Tool execution evidence | ToolCallLog, job, work-order and bounded dedupe IDs; bounded tool/status/result labels; never arguments or raw results |
| `provider` | AI or communications Provider routing/result evidence | audit/message/AI-turn IDs, bounded RFC/Provider message correlation and route labels; never Provider request/response payloads |
| `dispatch` | Operations Dispatch routing, claim, attempt and result evidence | outbox/rule/dispatch/group hashes, bounded dispatch/routing reference objects |
| `internal_audit` | ticket lifecycle, escalation, handoff and internal controls | ticket/conversation/handoff/operator IDs, bounded status labels and approved case-context state |

Every payload receives server-owned metadata:

```json
{
  "event_contract": "nexus.ticket_event.writer.v1",
  "event_class": "internal_audit",
  "schema_version": 1
}
```

Caller-provided values cannot override these fields.

## Classification authority

`app.services.ticket_event_classification.resolve_ticket_event_class` is shared by generic live writes and historical repair.

- EventTypes with one accepted meaning map deterministically.
- Customer comments require explicit internal/external visibility; Email inbound uses its controlled field identity.
- Ambiguous `field_updated` and `internal_note_added` events require one high-confidence server-owned signature from field name, bounded payload keys or note vocabulary.
- A missing signature fails as unclassified.
- Conflicting signatures fail as ambiguous.
- Existing governed metadata may be reused only when it does not contradict either an unambiguous EventType or one high-confidence Dispatch, Tool, Tracking, Provider or Internal Audit evidence signature.
- Generic `log_event()` does not default ambiguous events to Internal Audit.

The server-owned QA workflow fields `qa_knowledge_gap` and `qa_agent_appeal` are explicit Internal Audit signatures. They are registered by exact name only; no permissive `qa_*` prefix exists. The dedicated TicketEvent gate executes the real QA training contract so future workflow fields remain fail closed until deliberately classified.

This prevents a valid Dispatch, Tool, Tracking or Provider event from being silently downgraded and stripped of its class-owned evidence, even when stale or caller-controlled governed metadata is already present.

### External customer comment authority

For `comment_added`, the relational `TicketComment.visibility` in the caller-owned transaction is the final classification authority. `TicketEventWriter.add(...)` validates that exactly one pending comment belongs to the ticket and actor, forces external comments to `customer_visible` and internal comments to `internal_audit`, materializes the server-owned `comment_id`, and validates any WebChat message/action join against the same ticket before sanitization.

The raw visitor body remains in the authoritative `WebchatMessage` and external `TicketComment`; the TicketEvent sink retains only approved joins such as `comment_id`, `conversation_id` and `webchat_message_id`. A caller-provided event class cannot downgrade an external comment or bypass this relational check.

Legacy `external_channel_*` EventType references in the classifier are read-only compatibility semantics. They remain exact-listed in the retirement inventory and do not authorize a legacy write surface or runtime reactivation.

## Data safety

The writer permits only explicit class-specific identifiers, low-cardinality labels and a small set of class-specific structured references. Structured values pass through the recursive Audit Sanitizer before serialization.

The boundary must not persist:

- raw customer email addresses, phone numbers, postal addresses or tracking numbers;
- authorization headers, API keys, tokens, cookies, credentials or secrets;
- WhatsApp group IDs or raw chat JIDs;
- Provider request/response payloads or unbounded runtime traces;
- Tool arguments, Tool results or raw external payloads;
- customer claims, previous AI prose or other unverified content as facts.

Safe operational joins such as database IDs, explicit hashes, bounded dedupe keys and low-cardinality status/reason codes may be retained only by the event class that owns them.

Customer-visible and Provider events have one narrow exception for operational message correlation. Only syntactically valid, bounded RFC Message-IDs/reference chains and opaque Provider Message IDs are copied. Provider references are rejected before the post-sanitization merge when they match API-key, bearer, token, credential, authorization, password or secret shapes. The generic identifier regex is not widened, invalid values are dropped, and the authoritative Email/Provider source row retains the full lifecycle state.

Internal Audit events have one equally narrow WebCall correlation exception: a bounded opaque `voice_session_id` may be copied after recursive sanitization. The authoritative WebCall session remains the source of full call state, invalid references are dropped, and generic identifier handling is not widened.

Current limits are enforced recursively and on final serialized UTF-8 bytes. ORM text fields are independently sanitized and bounded:

| Field | Maximum |
|---|---:|
| `field_name` | 120 characters |
| `old_value` | 500 characters |
| `new_value` | 500 characters |
| `note` | 1,000 characters |
| `payload_json` | 8 KiB serialized |

## Architecture enforcement

`backend/tests/test_ticket_event_architecture.py` parses every Python file under `backend/app` and rejects direct or aliased `TicketEvent(...)` construction. It resolves explicit imports, aliased imports, `import app.models`, `import app.models as ...`, `from app import models` and star imports. The only allowed construction sites are the ORM declaration and `ticket_event_writer.py`.

Querying `TicketEvent` remains legal. New production writes that bypass the writer fail the dedicated `ticket-event-persistence-gate` workflow.

## Historical dry-run and repair

The offline tool is:

```bash
PYTHONPATH=backend python backend/scripts/repair_ticket_events.py \
  --tenant-id <explicit-tenant> \
  --batch-size 200 \
  --max-events 10000
```

Dry-run is the default. Mutation requires the explicit `--apply` flag:

```bash
PYTHONPATH=backend python backend/scripts/repair_ticket_events.py \
  --tenant-id <explicit-tenant> \
  --batch-size 200 \
  --max-events 10000 \
  --apply \
  --output artifacts/ticket-event-repair-summary.json
```

Safety rules:

1. `--tenant-id` is mandatory and `default` is rejected.
2. Ticket scope is resolved from server-owned `CaseContextRecord` and `RuntimeDecisionAuditRecord` associations.
3. Missing or cross-Tenant ambiguous ownership fails before event payloads are read.
4. Selection joins through authorized Ticket IDs and advances by event ID in bounded batches.
5. Unknown EventTypes and unclassified/ambiguous class signatures are counted and never mutated.
6. The same classifier governs live generic writes and repair mapping v2.
7. The plan stores original/replacement digests, not raw payloads.
8. Apply revalidates and locks the server-owned Tenant scope, locks each selected TicketEvent row, and verifies an original digest that includes event identity and content before mutation.
9. Re-running after a successful apply yields zero planned changes.
10. Output is a bounded aggregate JSON summary; it never prints payloads, contact data or customer identifiers.
11. The tool is not called by application startup, Alembic or deployment workflows.

Because first-class Tenant ownership remains governed separately, the resolver intentionally rejects Tenant scopes that cannot be established from the current server-owned associations. It never falls back to a global scan.

## Verification

The dedicated exact-head workflow compiles Writer, payload-reference validation, classifier and repair, then runs:

- writer/classification/architecture/repair truth tables;
- Customer-visible event safety;
- Email workbench lifecycle;
- QA training knowledge-gap and appeal audit contracts;
- Speedaf controlled actions;
- Auto Ticket, Dispatch Outbox, Tool execution and WhatsApp routing;
- WebChat handoff and WebCall/voice callers.

It uploads bounded JUnit evidence even when a caller regression fails. Broad backend, PostgreSQL, security, release-image and product integration checks remain independently required.

## Rollback

Revert the implementation PR. No schema or enum migration is involved, so existing rows remain readable. No repair runs automatically, and reverting the code does not replay or duplicate any customer-visible, Provider, Tool or Dispatch action.

A separately authorized historical repair execution must retain its bounded summary and database backup/restore evidence. Code rollback does not attempt to reconstruct payload content deliberately removed for safety.
