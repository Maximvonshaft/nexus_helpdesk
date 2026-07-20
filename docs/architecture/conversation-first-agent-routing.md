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
- `ticket.create` creates or reuses a ticket only after the configured policy permits it.

The model proposes tools. The controlled executor validates policy, scope, confirmation, idempotency, and handler availability before executing them.

## Delivery boundaries

This delivery is WebChat-first. It preserves current ticket-backed WhatsApp behavior until outbound routing can be conversation-bound without weakening provider safety. It does not introduce a parallel WhatsApp conversation implementation.

The delivery must include:

- reversible Alembic migration;
- ticketless WebChat initialization and AI execution;
- ticketless handoff lifecycle;
- operator presence, heartbeat, capacity, and FIFO assignment;
- conversation closure outcomes;
- availability and ticket tools;
- one canonical frontend presence control;
- PostgreSQL concurrency tests and existing canonical acceptance.

## Retirement rule

Any old WebChat path that assumes a ticket is mandatory must be changed at its source or reduced to a thin backward-compatible wrapper. No duplicate live path may remain.