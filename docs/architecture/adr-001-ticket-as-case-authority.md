# ADR-001: Conversation and Ticket-as-Case authority

- Status: Accepted
- Effective date: 2026-07-27
- Decision owner: Nexus product and architecture authority
- Supersedes: any statement that treats every customer contact as a durable Case

## Context

Nexus supports live conversations that may be completed immediately and durable logistics responsibilities that can outlive a channel session. Using the word `Case` for both concepts created ambiguity across Workspace copy, ownership, handoff, SLA, closure and reporting.

Creating another `cases` table would introduce a second durable work authority and would duplicate Ticket identity, ownership and lifecycle. The repository therefore adopts the following single model.

## Decision

### Conversation

`WebchatConversation` is the live communication identity.

- It may contain text, voice, Agent turns and human participation.
- It may finish without creating durable work.
- It does not own durable SLA, business-action completion or Safe Effective Closure.
- Its `active_ai_*` and handoff display fields are bounded read caches, not independent business state machines.

### Ticket-as-Case

`Ticket` is the only durable Case aggregate.

A Ticket is created or reused only when at least one of the following is true:

1. asynchronous follow-up is required;
2. a governed business action requires a formal record;
3. policy or regulation requires durable responsibility;
4. the operator explicitly accepts durable ownership.

The stable Case identifier is `Ticket.id`; APIs may expose it as `case_id` but may not create another Case identity.

### Handoff

`WebchatHandoffRequest` is the only mutable lifecycle authority for transferring a live Conversation between Agent and human handling.

- Assignment, acceptance, release, close, expiry and resume-to-Agent commands mutate this aggregate.
- Conversation fields may cache the current Handoff version.
- Ticket fields may describe durable Case ownership but may not duplicate the live Handoff state machine.

### OperatorTask

`OperatorTask` is a rebuildable read projection.

- It provides queue ordering, filtering and management presentation.
- It does not authorize or execute source-domain transitions.
- Source-domain commands must be sent to the corresponding aggregate service.
- Projection rows may be deleted and rebuilt without losing business truth.

## User language

- A live contact without a Ticket is a **conversation** (`会话`).
- A durable Ticket is a **case** (`案例`).
- A Handoff is a transfer of live handling, not a new Case.
- A queue row is a task projection, not a business record.

## Writer matrix

| Fact | Sole mutable writer | Read projections |
|---|---|---|
| Live communication lifecycle | Conversation service | Workspace thread, channel counters |
| Durable Case lifecycle and ownership | Ticket service | Workspace Case spine, Control Tower |
| Live human handoff | Handoff service | Conversation snapshot, OperatorTask |
| Queue presentation | Projection/rebuild service | Workspace queue, Control Tower |
| Business action and outcome | Case outcome ledger service | Timeline, closure assessment, metrics |
| Safe Effective Closure | Closure assessment service | Ticket closure receipt, management metrics |

## Consequences

- No `Case` ORM model, table, route or state machine may be added.
- Ticketless Conversations remain first-class and are not backfilled with synthetic Tickets.
- Projection repair must never mutate source-domain facts.
- New channel implementations attach to Conversation and create a Ticket only through the canonical governed command.
- Documentation, API schemas and UI copy must preserve the distinction.

## Verification

`backend/tests/test_business_object_authority.py` enforces the machine-readable authority, required wording and absence of a second Case model/table.
