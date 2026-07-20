# Conversation-First Agent Routing

## Purpose

Nexus must treat a customer conversation and a support ticket as different business objects.

- A **conversation** is the live communication record between the customer, AI, and an operator.
- A **ticket** is a durable follow-up responsibility that remains after the live conversation cannot finish the work.
- A **handoff** transfers a live conversation from AI to a human operator. A handoff does not require a ticket.

This design establishes one canonical routing and capacity authority. It does not create a second inbox, queue, message transport, permission system, or ticket workflow.

## Product invariants

1. A WebChat conversation can be created, receive messages, run AI turns, and close without a ticket.
2. A human handoff can be requested, queued, accepted, released, resumed to AI, or closed without a ticket.
3. A ticket is created only when the issue requires asynchronous follow-up, a controlled business action, or a formal record.
4. `webchat_conversations.ticket_id` remains the simple optional primary link for this delivery. A many-to-many link model is not introduced until a proven business requirement exists.
5. Agent occupancy is derived from accepted, non-terminal handoffs. There is no mutable parallel counter.
6. Agent availability is derived from server-owned presence state, heartbeat freshness, configured capacity, current occupancy, and authorized scope.
7. Waiting handoffs are selected FIFO inside the eligible tenant/country/channel scope.
8. Assignment is concurrency-safe. Capacity and handoff state are rechecked while rows are locked before ownership is committed.
9. Closing a human conversation records an outcome and releases capacity. "Conversation ended" and "customer issue resolved" are separate facts.
10. Ticket Safe Effective Closure remains ticket-only and does not govern ordinary conversation closure.

## Canonical lifecycle

### AI resolves the conversation

`open -> ai_active -> closed(ai_resolved)`

No ticket is created.

### Human resolves the conversation live

`open -> handoff_requested -> human_active -> closed(human_resolved)`

No ticket is created.

### Follow-up is required

`open -> handoff_requested|ai_active -> ticket_created -> closed(ticket_created)`

The existing governed `ticket.create` action creates or reuses the ticket and binds its id to the conversation.

### Voice is explicitly initiated

Ordinary text conversations remain ticketless. When the customer actually initiates the existing voice workflow, Nexus lazily creates or reuses the ticket required by the ticket-backed voice authority, then continues through the existing voice queue. Opening WebChat alone never creates that ticket.

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

## AI tools

The existing governed tool registry remains canonical.

- `support.availability` returns a safe aggregate: online agents, total capacity, occupied capacity, available capacity, queue length, and the current request position when available.
- `handoff.request.create` creates or updates a ticketless handoff request.
- `ticket.create` first returns `customer_confirmation_required` for ordinary cases. A controlled caller must explicitly provide `customer_confirmation_granted=true` before the executor may create or reuse the ticket.

The model proposes tools. The controlled executor validates policy, scope, confirmation, idempotency, and handler availability before executing them.

## Runtime, evidence, and audit

Conversation is the primary runtime identity. Ticket is optional context.

- Ticketless AI turns, messages, events, handoffs, OSR decisions, Case Context records, Debug Runs, and Test Findings persist with `ticket_id = NULL`.
- Runtime generation rechecks the server-owned conversation state immediately before creating a public AI message. A human takeover or superseding customer message suppresses the stale reply.
- Runtime traces and customer-visible message metadata are sanitized before persistence.
- Failure of the OSR audit path is non-blocking for an otherwise permitted customer-visible reply, while the failure remains observable.

## Delivery boundaries

This delivery is WebChat-first. It preserves current ticket-backed WhatsApp behavior until outbound routing can be conversation-bound without weakening provider safety. It does not introduce a parallel WhatsApp conversation implementation.

The delivered foundation includes:

- reversible Alembic migration through revision `20260720_0064`;
- ticketless WebChat initialization and AI execution;
- ticketless handoff lifecycle;
- operator presence, heartbeat, capacity, and FIFO assignment;
- conversation closure outcomes and capacity release;
- aggregate availability and customer-confirmed ticket tools;
- one canonical frontend presence control;
- lazy ticket creation only when the existing voice workflow is initiated;
- ticketless OSR audit, Case Context, Debug Bundle, and Test Finding support.

## Verification contract

The acceptance suite must prove at least these business outcomes:

1. Creating and using text WebChat does not create a ticket.
2. Ticketless AI replies are persisted, audited, redacted, and visible in the debug bundle.
3. A human takeover during AI generation prevents the AI reply from being committed.
4. A ticketless handoff can enter the unified queue and be assigned according to online state, heartbeat, capacity, scope, and FIFO order.
5. Closing one accepted conversation releases one slot and assigns the next eligible request.
6. `ticket.create` cannot execute without recorded customer confirmation and is idempotent after confirmation.
7. Initiating voice creates or reuses the necessary ticket without restoring automatic ticket creation for text chat.
8. PostgreSQL migration, concurrency, complete backend regression, frontend verification, browser journeys, security checks, and image smoke all pass for the exact candidate Head.

## Retirement rule

Any old WebChat path that assumes a ticket is mandatory must be changed at its source or reduced to a thin backward-compatible wrapper. No duplicate live path may remain. Temporary patch, export, or migration helper files are not part of the final tree.
