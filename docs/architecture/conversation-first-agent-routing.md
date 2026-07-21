# Conversation-First Agent Routing

## Purpose

Nexus treats a customer conversation and a support ticket as different business objects.

- A **conversation** is the live communication record between the customer, AI, and an operator.
- A **ticket** is a durable follow-up responsibility that remains after the live conversation cannot finish the work.
- A **handoff** transfers a live conversation from AI to a human operator. A handoff does not require a ticket.
- A **voice session** is a real-time communication session attached to a conversation. Starting or operating a voice session does not create a ticket.

This design establishes one canonical routing and capacity authority. It does not create a second inbox, queue, message transport, permission system, ticket workflow, or voice state machine.

## Product invariants

1. A WebChat conversation can be created, receive messages, run Agent turns, start voice, and close without a ticket.
2. A human handoff can be requested, queued, accepted, released, resumed to AI, or closed without a ticket.
3. A ticket is created only when the issue requires asynchronous follow-up, a controlled business action, or a formal record.
4. `webchat_conversations.ticket_id` remains the optional primary link. A many-to-many link model is not introduced without a proven business requirement.
5. Agent occupancy is derived from accepted, non-terminal handoffs. There is no mutable parallel counter.
6. Agent availability is derived from server-owned presence state, heartbeat freshness, configured capacity, current occupancy, and authorized scope.
7. Waiting handoffs are selected FIFO inside the eligible tenant/country/channel scope.
8. Assignment is concurrency-safe. Capacity and handoff state are rechecked while rows are locked before ownership is committed.
9. Closing a human conversation records an outcome and releases capacity. “Conversation ended” and “customer issue resolved” are separate facts.
10. Ticket Safe Effective Closure remains ticket-only and does not govern ordinary conversation closure.
11. Voice control is session-first. Ticket visibility is consulted only when a voice session already has a ticket; otherwise visibility comes from the conversation tenant/country/channel scope.
12. A ticket-required business action fails closed when no ticket exists. The system must not create a ticket merely to satisfy a technical route or service signature.

## Canonical lifecycle

### Agent resolves the conversation

`open -> ai_active -> closed(ai_resolved)`

No ticket is created.

### Human resolves the conversation live

`open -> handoff_requested -> human_active -> closed(human_resolved)`

No ticket is created.

### Follow-up is required

`open -> handoff_requested|ai_active -> ticket_created -> closed(ticket_created)`

The governed `ticket.create` action creates or reuses the ticket and binds its id to the conversation only after trusted server-side confirmation.

### Voice is explicitly initiated

`conversation -> voice_session(requested) -> accepted|rejected|ended`

The existing `WebchatVoiceSession` lifecycle remains the only voice authority. Operator actions use session-first routes:

- `/admin/voice/{voice_session_id}/accept`
- `/admin/voice/{voice_session_id}/reject`
- `/admin/voice/{voice_session_id}/end`
- `/admin/voice/{voice_session_id}/evidence`
- `/admin/voice/{voice_session_id}/actions`
- `/admin/voice/{voice_session_id}/notes`

A voice session may reference a ticket that already exists, but voice initiation never creates or backfills one. A provider callback or another controlled action that genuinely requires a formal ticket returns an explicit ticket-required failure when the session is ticketless.

## Agent presence and capacity

Each operator has one server-owned state:

- `online`: may receive new conversations.
- `paused`: may finish current conversations but receives no new assignments.
- `offline`: unavailable for assignment.

A fresh browser heartbeat is required for assignment. Stale presence is treated as unavailable without rewriting the operator's chosen status.

Capacity is calculated as:

`available_slots = max_concurrent_conversations - accepted_open_handoffs`

The default maximum is 3 and is bounded to 1..20.

## Queue and assignment

The existing `WebchatHandoffRequest` and `OperatorTask` records remain the only handoff queue authorities.

When a handoff is requested:

1. Suspend AI and create or reuse the open handoff request.
2. Attempt automatic assignment to an eligible online operator with free capacity.
3. If none is available, keep the request in `requested` status.
4. Order waiting requests by `requested_at`, then `id`.
5. When an operator comes online or a slot is released, assign the oldest eligible request.

Eligibility requires matching server-owned queue scope. FIFO applies after scope eligibility.

## Agent Tools

The governed Tool Registry and private Tool Executor Core remain canonical.

- `support.availability` returns a safe aggregate: online agents, total capacity, occupied capacity, available capacity, queue length, and the current request position when available.
- `handoff.request.create` creates or updates a ticketless handoff request.
- `ticket.create` requires trusted server-side customer confirmation before it may create or reuse a ticket.
- Public WebChat exposes only least-privilege Tools that it can authorize end to end. Confirmation-required Tools are not exposed to that principal until a server-issued confirmation artifact exists.

The model proposes Tools. The server validates registration, JSON input schema, availability, permission, confirmation, risk, idempotency, handler authority, redaction, and audit before execution.

## Runtime, evidence, and audit

Conversation is the primary runtime identity. Ticket is optional context.

- Ticketless Agent turns, messages, events, handoffs, OSR decisions, Case Context records, Debug Runs, Test Findings, and voice sessions persist with `ticket_id = NULL`.
- Ticket-backed and ticketless text paths use the same bounded Generic Agent loop.
- Runtime generation rechecks the server-owned conversation state immediately before creating a public Agent message. A human takeover or superseding customer message suppresses the stale reply.
- Runtime traces, Tool observations, and customer-visible message metadata are sanitized before persistence.
- A Tool result is returned to the model as successful only after its database transaction commits.
- Provider/runtime failure still produces one deterministic customer-visible terminal response.

## Delivery boundaries

This delivery is WebChat-first. It preserves current ticket-backed WhatsApp behavior until outbound routing can be conversation-bound without weakening provider safety. It does not introduce a parallel WhatsApp conversation implementation.

The delivered foundation includes:

- one Alembic head through revision `20260720_0067`;
- ticketless WebChat initialization and Generic Agent execution;
- ticketless handoff lifecycle;
- operator presence, heartbeat, capacity, and FIFO assignment;
- conversation closure outcomes and capacity release;
- aggregate availability and governed ticket tools;
- one canonical frontend presence control;
- session-first, ticket-optional voice control;
- ticketless OSR audit, Case Context, Debug Bundle, and Test Finding support.

## Verification contract

The acceptance suite must prove at least these business outcomes:

1. Creating and using text WebChat does not create a ticket.
2. Ticketless Agent replies are persisted, audited, redacted, and visible in the debug bundle.
3. A human takeover during Agent generation prevents the stale reply from being committed.
4. A ticketless handoff can enter the unified queue and be assigned according to online state, heartbeat, capacity, scope, and FIFO order.
5. Closing one accepted conversation releases one slot and assigns the next eligible request.
6. `ticket.create` cannot execute without trusted server-side confirmation and is idempotent after confirmation.
7. Starting, accepting, rejecting, annotating, or ending a ticketless voice session never creates a ticket.
8. A ticket-required voice business action fails closed when the session has no ticket.
9. Tool arguments that do not match the registered JSON Schema are blocked before handler execution.
10. PostgreSQL migration, complete backend regression, frontend verification, browser journeys, security checks, and image smoke all pass for the exact candidate Head.

## Retirement rule

Any old path that assumes a ticket is mandatory for text, handoff, or voice must be changed at its source or physically deleted. No duplicate live path, compatibility executor, automatic voice-ticket adapter, temporary patch, export, migration helper, or shadow runtime may remain in the accepted tree.
