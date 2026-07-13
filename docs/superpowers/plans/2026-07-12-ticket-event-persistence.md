# Governed TicketEvent Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `TicketEventWriter` the only legal production creation boundary, enforce versioned class-specific bounded persistence, and deliver deterministic scope-bound historical dry-run/repair tooling.

**Architecture:** Add one writer that constructs and flushes `TicketEvent` rows without committing. Extend the existing recursive sanitizer with immutable event-class policies, migrate every production constructor to the writer, enforce the boundary with AST tests/CI, and implement an offline repair engine whose CLI fails closed unless an authoritative Tenant ticket scope is supplied.

**Tech Stack:** Python 3.11, SQLAlchemy ORM, pytest, Python `ast`, GitHub Actions, existing `AuditSanitizer` and `TicketEvent` model.

## Global Constraints

- Contract identifier is exactly `nexus.ticket_event.writer.v1`.
- Supported classes are exactly `customer_visible`, `tracking`, `tool`, `provider`, `dispatch`, and `internal_audit`.
- No database schema or Alembic change.
- The writer may `add` and `flush`; it must never `commit` or `rollback`.
- Unknown class/type, unsupported objects, cyclic data, invalid serialization and unresolved Tenant scope fail closed.
- No raw tracking/contact/address/credential/Provider payload/Tool argument/Tool result data may enter durable `payload_json`, `note`, `old_value` or `new_value`.
- Historical repair defaults to dry-run and is never invoked by startup or migration.
- No deploy, real outbound, Provider enablement, production mutation or release GO.

---

### Task 1: Establish RED writer and policy tests

**Files:**
- Create: `backend/tests/test_ticket_event_writer.py`
- Create: `backend/tests/test_ticket_event_architecture.py`
- Create: `backend/tests/test_ticket_event_repair.py`
- Create: `.github/workflows/ticket-event-persistence-gate.yml`

**Interfaces:**
- Consumes: existing `EventType`, `TicketEvent`, `sanitize_ticket_event_payload`, SQLite-compatible SQLAlchemy models.
- Produces test expectations for `TicketEventClass`, `TicketEventWriter`, `TicketEventPolicy`, `plan_ticket_event_repairs`, and `apply_ticket_event_repairs`.

- [ ] **Step 1: Write failing unit tests for the desired writer API**

```python
from app.enums import EventType
from app.services.ticket_event_writer import TicketEventClass, TicketEventWriter


def test_writer_injects_server_owned_contract_metadata_and_redacts_tool_payload():
    row = TicketEventWriter.build(
        ticket_id=42,
        event_type=EventType.internal_note_added,
        event_class=TicketEventClass.TOOL,
        payload={
            "tool_call_log_id": 9,
            "tool_name": "tracking.lookup",
            "status": "executed",
            "arguments": {"tracking_number": "CH020000129131"},
            "result": {"phone": "+38267123456"},
        },
    )
    payload = json.loads(row.payload_json)
    assert payload["event_contract"] == "nexus.ticket_event.writer.v1"
    assert payload["event_class"] == "tool"
    assert payload["schema_version"] == 1
    assert payload["tool_call_log_id"] == 9
    assert "CH020000129131" not in row.payload_json
    assert "+38267123456" not in row.payload_json
```

Add one parameterized test covering all six classes, hostile cycle/Unicode/oversize cases, server metadata override attempts, invalid class/type, text-field redaction/truncation, and `add()` calling `add`/`flush` but never `commit`.

- [ ] **Step 2: Write failing AST architecture test**

```python
violations = find_direct_ticket_event_construction(APP_ROOT)
assert violations == [], "\n".join(violations)
```

The helper must parse every `backend/app/**/*.py`, resolve direct and aliased imports from `app.models`, exclude only `models.py` and `services/ticket_event_writer.py`, and report POSIX path plus line number.

- [ ] **Step 3: Write failing repair-engine tests**

Use in-memory rows or a temporary SQLite database to prove:

```python
plan = plan_ticket_event_repairs(rows, tenant_id="tenant-a", ticket_ids={1, 2})
assert plan.changed_count == 1
assert plan.summary_json does not contain raw payload values

applied = apply_ticket_event_repairs(rows, plan)
assert applied.changed_count == 1
assert plan_ticket_event_repairs(rows, tenant_id="tenant-a", ticket_ids={1, 2}).changed_count == 0
```

Also assert missing, empty or ambiguous Tenant ticket scope raises a bounded `TicketEventRepairScopeError` before reading event payloads.

- [ ] **Step 4: Add the dedicated immutable CI workflow**

The workflow must use immutable `actions/checkout` and `actions/setup-python` SHAs already accepted in the repository, set `PYTHONPATH=backend`, install `backend/requirements.txt`, compile changed modules, run the three new tests plus `test_customer_visible_event_safety.py`, and run exact-head `git diff --check`.

- [ ] **Step 5: Run RED verification**

Run:

```bash
pytest -q \
  backend/tests/test_ticket_event_writer.py \
  backend/tests/test_ticket_event_architecture.py \
  backend/tests/test_ticket_event_repair.py
```

Expected: FAIL because `app.services.ticket_event_writer`, architecture helper and repair APIs are absent, and because current production direct constructors remain.

- [ ] **Step 6: Commit RED evidence**

```bash
git add backend/tests/test_ticket_event_*.py .github/workflows/ticket-event-persistence-gate.yml
git commit -m "test(audit): define governed TicketEvent persistence"
```

---

### Task 2: Implement class-specific sanitizer policy and writer

**Files:**
- Modify: `backend/app/services/ticket_event_sanitizer.py`
- Create: `backend/app/services/ticket_event_writer.py`
- Test: `backend/tests/test_ticket_event_writer.py`

**Interfaces:**
- Produces:
  - `TicketEventClass(str, Enum)`
  - `TicketEventPolicy(frozen=True)`
  - `TICKET_EVENT_CONTRACT = "nexus.ticket_event.writer.v1"`
  - `TicketEventWriter.build(...) -> TicketEvent`
  - `TicketEventWriter.add(db, ...) -> TicketEvent`
  - `sanitize_ticket_event_payload(value, *, policy=None) -> dict[str, Any]`
  - `serialize_ticket_event_payload(value, *, policy=None) -> str`

- [ ] **Step 1: Extend sanitizer policy without breaking the existing default API**

Add an immutable policy carrying class, schema version, safe identifier keys, safe label keys, recursive limits and byte limit. Existing calls without a policy retain the current Customer-visible-safe behavior.

Server-owned metadata must be written after recursive sanitization:

```python
sanitized["event_contract"] = "nexus.ticket_event.writer.v1"
sanitized["event_class"] = policy.event_class
sanitized["schema_version"] = policy.schema_version
```

Fallback markers must preserve the same metadata and only policy-approved identifiers.

- [ ] **Step 2: Implement writer validation and field sanitization**

```python
class TicketEventWriter:
    @classmethod
    def build(cls, *, ticket_id, event_type, event_class, actor_id=None,
              field_name=None, old_value=None, new_value=None, note=None,
              payload=None, created_at=None):
        policy = policy_for(event_class)
        return TicketEvent(
            ticket_id=require_positive_int(ticket_id),
            actor_id=optional_non_negative_int(actor_id),
            event_type=require_event_type(event_type),
            field_name=safe_field_name(field_name),
            old_value=safe_event_text(old_value, limit=500),
            new_value=safe_event_text(new_value, limit=500),
            note=safe_event_text(note, limit=1000),
            payload_json=serialize_ticket_event_payload(payload or {}, policy=policy),
            **({"created_at": created_at} if created_at is not None else {}),
        )
```

`add` calls `build`, `db.add`, `db.flush`, and returns the row. It must not catch database errors or own commit/rollback.

- [ ] **Step 3: Run writer tests GREEN**

```bash
pytest -q backend/tests/test_ticket_event_writer.py backend/tests/test_customer_visible_event_safety.py
```

Expected: PASS with no warning or unbounded output.

- [ ] **Step 4: Commit the writer boundary**

```bash
git add backend/app/services/ticket_event_sanitizer.py backend/app/services/ticket_event_writer.py backend/tests/test_ticket_event_writer.py
git commit -m "feat(audit): add governed TicketEvent writer"
```

---

### Task 3: Migrate every production constructor

**Files:**
- Modify every production file reported by the RED AST test, including current direct writers under `backend/app/api/` and `backend/app/services/`.
- Modify: `backend/app/services/audit_service.py`
- Modify: `backend/app/services/customer_visible_message_service.py`
- Test: existing focused tests for each touched service.

**Interfaces:**
- Consumes: `TicketEventWriter.add` and explicit `TicketEventClass`.
- Produces: zero authorized direct constructors outside the writer/model declaration.

- [ ] **Step 1: Convert the compatibility audit facade**

`log_event` delegates to the writer. Add a keyword-only `event_class` with the safe default `TicketEventClass.INTERNAL_AUDIT` so legacy lifecycle callers do not regress. Remove local JSON serialization.

- [ ] **Step 2: Convert Customer-visible writes**

Replace direct construction with:

```python
ticket_event = TicketEventWriter.add(
    db,
    ticket_id=ticket.id,
    actor_id=created_by,
    event_type=event_type,
    event_class=TicketEventClass.CUSTOMER_VISIBLE,
    note=event_note,
    payload=payload,
)
```

Preserve return types, flush timing and surrounding transaction ownership.

- [ ] **Step 3: Convert domain writers with explicit classes**

Use exact ownership:

- Tracking source/result evidence → `TRACKING`
- controlled Tool decisions/results and timeline Tool actions → `TOOL`
- AI/communication Provider route/result evidence → `PROVIDER`
- Operations Dispatch enqueue/attempt/result → `DISPATCH`
- escalation, handoff, background, ticket lifecycle and internal control → `INTERNAL_AUDIT`

Do not derive class from `EventType` at runtime. Remove only imports made obsolete by the conversion.

- [ ] **Step 4: Run architecture and touched-service tests**

```bash
pytest -q backend/tests/test_ticket_event_architecture.py backend/tests/test_customer_visible_event_safety.py
```

Then run each existing test module corresponding to a changed production service. Expected: PASS; architecture violations list is empty.

- [ ] **Step 5: Commit caller migration**

```bash
git add backend/app backend/tests
git commit -m "refactor(audit): route TicketEvent writes through governed boundary"
```

---

### Task 4: Implement deterministic historical repair engine and fail-closed CLI

**Files:**
- Create: `backend/app/services/ticket_event_repair.py`
- Create: `backend/scripts/repair_ticket_events.py`
- Test: `backend/tests/test_ticket_event_repair.py`

**Interfaces:**
- Produces:
  - `TicketEventRepairScopeError`
  - `TicketEventRepairDecision`
  - `TicketEventRepairPlan`
  - `plan_ticket_event_repairs(...)`
  - `apply_ticket_event_repairs(...)`
  - `resolve_authorized_ticket_ids(...)`

- [ ] **Step 1: Implement exact versioned class mapping for historical rows**

Map each current `EventType` to a class in a constant whose version is included in the summary. Do not use substring or fuzzy inference. An unknown value returns `unclassified` and is never changed.

- [ ] **Step 2: Implement canonical planning**

For each row already restricted to authorized ticket IDs:

1. Parse `payload_json`; invalid JSON becomes a bounded invalid marker.
2. Re-sanitize under the exact class policy.
3. Preserve approved existing ORM fields through writer text helpers.
4. Compare canonical serialized output to stored output.
5. Record only event ID, class, reason and replacement digest in the plan; never copy payload content into the plan summary.

Sort decisions by event ID. Bound all counters and reason labels.

- [ ] **Step 3: Implement idempotent apply**

Apply only decisions whose expected original digest still matches the row. A mismatch fails closed to avoid overwriting concurrent changes. Update `payload_json` and governed text fields only; do not create a new event and do not trigger external actions.

- [ ] **Step 4: Implement Tenant scope resolver interface and CLI**

The CLI requires `--tenant-id`. It obtains ticket IDs through a server-owned resolver. Until #546 provides authoritative first-class Tenant ownership, unresolved/ambiguous mappings raise `TicketEventRepairScopeError`; there is no `default`, empty or global fallback.

Supported options:

```text
--tenant-id <required>
--batch-size <1..1000, default 200>
--after-id <non-negative, default 0>
--max-events <1..100000, default 10000>
--apply <explicit mutation flag>
--output <optional bounded JSON summary path>
```

- [ ] **Step 5: Run repair tests GREEN**

```bash
pytest -q backend/tests/test_ticket_event_repair.py
```

Expected: PASS for deterministic ordering, dry-run, apply, re-run zero-change, digest conflict, invalid JSON, unclassified type, foreign ticket exclusion, unresolved Tenant failure and bounded summary.

- [ ] **Step 6: Commit repair tooling**

```bash
git add backend/app/services/ticket_event_repair.py backend/scripts/repair_ticket_events.py backend/tests/test_ticket_event_repair.py
git commit -m "feat(audit): add scoped TicketEvent repair tooling"
```

---

### Task 5: Complete documentation, full focused verification and review evidence

**Files:**
- Create: `docs/engineering/ticket-event-persistence-contract.md`
- Modify: `.github/workflows/ticket-event-persistence-gate.yml`
- Update PR and Work Item evidence only after tests run.

**Interfaces:**
- Produces operator-facing contract, exact commands, repair safety/rollback and CI authority.

- [ ] **Step 1: Document the final contract**

Include class table, writer API, prohibited data, transaction semantics, architecture rule, repair dry-run/apply procedure, bounded output schema, rollback and examples that contain synthetic values only.

- [ ] **Step 2: Run focused exact-head suite**

```bash
python -m py_compile \
  backend/app/services/ticket_event_sanitizer.py \
  backend/app/services/ticket_event_writer.py \
  backend/app/services/ticket_event_repair.py \
  backend/scripts/repair_ticket_events.py
pytest -q \
  backend/tests/test_ticket_event_writer.py \
  backend/tests/test_ticket_event_architecture.py \
  backend/tests/test_ticket_event_repair.py \
  backend/tests/test_customer_visible_event_safety.py
git diff --check "${BASE_SHA}...${HEAD_SHA}"
```

Expected: all PASS and no whitespace findings.

- [ ] **Step 3: Run touched-domain regressions and repository CI**

Run all test modules associated with changed callers, then wait for repository-required checks on the exact PR head. Treat any stale-head result as non-authoritative.

- [ ] **Step 4: Perform two-stage review**

First review exact-head compliance against #566 and this plan. Then conduct a separate code-quality review for policy gaps, transaction changes, hidden direct writes, unsafe output, repair concurrency and rollback. Critical or important findings block ready-for-review status.

- [ ] **Step 5: Publish delivery evidence**

Update the PR with exact base/head, changed resources, RED and GREEN evidence, tests, no-migration statement, repair non-execution, rollback and material unverified items. Update #566 to `Lifecycle: In Review` and `Current PR: #...` only when the implementation is ready for review.

- [ ] **Step 6: Finish branch safely**

Do not merge until exact-head checks, review threads and required independent review settle. Merge with expected head SHA only. After merge, re-read main, #566, #545, related Epics and #548, and confirm #566 closed through `Closes #566`.