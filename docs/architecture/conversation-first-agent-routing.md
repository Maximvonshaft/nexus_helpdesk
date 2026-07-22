# Conversation-First Agent Routing

## Purpose

Nexus treats a customer Conversation and a Ticket as different business objects.

- A **Conversation** is the live communication record between the customer, Agent and operator.
- A **Ticket** is a durable follow-up responsibility created only when live interaction cannot complete the work or a governed business action requires a formal record.
- A **Handoff** transfers a live Conversation from Agent to operator. It does not require a Ticket.
- A **Voice Session** is attached to a Conversation. Starting or operating voice does not create a Ticket.

This architecture establishes one routing, capacity, Agent execution and message-persistence authority. It does not create a second inbox, queue, HTTP transport, permission system, Ticket workflow or voice state machine.

## Product invariants

1. A WebChat Conversation can be created, receive messages, run Agent turns, start voice and close without a Ticket.
2. A Handoff can be requested, queued, accepted, released, resumed to Agent or closed without a Ticket.
3. A Ticket is created only for asynchronous follow-up, a controlled business action or a formal record.
4. `webchat_conversations.ticket_id` is an optional link, not the primary runtime identity.
5. Operator occupancy is derived from accepted non-terminal handoffs. There is no parallel mutable counter.
6. Availability is derived from server-owned presence, heartbeat freshness, configured capacity, current occupancy and authorized scope.
7. Waiting handoffs are FIFO within eligible tenant, country and channel scope.
8. Assignment rechecks eligibility and capacity while rows are locked.
9. Conversation closure and Ticket Safe Effective Closure are distinct business facts.
10. Voice control is session-first and ticket-optional.
11. A Ticket-required Tool fails closed when no Ticket exists; the runtime must not manufacture a Ticket for a technical dependency.
12. Ticket-backed historical Conversations and ticketless Conversations use the same Agent reply and persistence authority.

## Canonical lifecycle

### Agent resolves live

```text
open -> ai_active -> closed(ai_resolved)
```

No Ticket is created.

### Operator resolves live

```text
open -> handoff_requested -> human_active -> closed(human_resolved)
```

No Ticket is created.

### Durable follow-up is required

```text
open -> ai_active|handoff_requested -> ticket.create -> closed(ticket_created)
```

The governed `ticket.create` Tool creates or reuses the Ticket only after server-side authorization and trusted confirmation.

### Voice is initiated

```text
conversation -> voice_session(requested) -> accepted|rejected|ended
```

Voice may reference an existing Ticket, but initiation never creates or backfills one.

## Canonical implementation authorities

- Session identity and visitor-token policy: `backend/app/services/webchat_session_identity.py`
- Conversation initialization and resume: `backend/app/services/conversation_first_service.py`
- Visitor messages and structured actions: `backend/app/services/webchat_message_service.py`
- Stable application facade: `backend/app/services/webchat_service.py`
- Agent turn orchestration: `backend/app/services/webchat_ai_orchestration_service.py`
- Agent reply execution and persistence: `backend/app/services/webchat_ai_service.py`
- Generic Agent provider bridge: `backend/app/services/webchat_runtime_ai_service.py`
- Operator thread and reply authority: `backend/app/services/conversation_operator_service.py`
- Handoff lifecycle: `backend/app/services/webchat_handoff_service.py`
- Operator assignment and capacity: the canonical operator queue and availability services
- Human voice lifecycle: `backend/app/services/webchat_voice_service.py`
- Live AI voice orchestration: `backend/app/services/live_voice_orchestration_service.py`

`webchat_service.py` is a bounded stable import facade. It must not contain Ticket creation, provider calls, policy evaluation, message persistence or fallback logic.

## Agent presence and capacity

Operator status is server-owned:

- `online`: eligible for new Conversations;
- `paused`: may finish current work but receives no new assignment;
- `offline`: unavailable.

A fresh browser heartbeat is required. Available capacity is:

```text
max_concurrent_conversations - accepted_open_handoffs
```

The configured maximum is bounded and assignment is concurrency-safe.

## Queue and assignment

`WebchatHandoffRequest` and `OperatorTask` remain the queue authorities.

1. Suspend Agent execution and create or reuse the Handoff request.
2. Attempt assignment to an eligible online operator with capacity.
3. Otherwise keep the request waiting.
4. Order eligible requests by `requested_at`, then `id`.
5. Reassign the oldest eligible request when capacity becomes available.

## Agent execution and evidence

Conversation is the primary runtime identity. Ticket is optional context.

- All text turns use one Generic Agent loop.
- All public replies pass through one customer-visible policy and terminal-fallback boundary.
- All text replies are persisted by one Agent reply authority, with only the final storage projection varying according to whether a Ticket already exists.
- Runtime state is rechecked before a public reply is committed.
- Handoff is a Tool side effect, not a reply-text shortcut.
- Runtime traces and Tool data are sanitized before persistence.
- Tool success is returned to the model only after its business transaction succeeds.
- Provider failure produces one deterministic customer-visible terminal response.
- A standalone model CLI, channel-specific model loop or Ticketless reply service is forbidden.

## Compatibility boundary

Historical Conversations may already reference Tickets. That data relationship is supported by the same canonical runtime; it is not a second initialization or Agent execution path.

Compatibility may read historical state but may not:

- create a Ticket for every new Conversation;
- select a separate Agent service by `ticket_id`;
- own customer-visible policy or fallback behavior;
- call a model or Provider independently;
- define a second message persistence path.

## Migration authority

Alembic is the only schema-mutation authority. The current migration head must be derived from `alembic heads` and exact release evidence; architecture documents must not hard-code a moving revision as current truth.

## Acceptance contract

Acceptance must prove:

1. New WebChat initialization does not create a Ticket.
2. Historical ticket-backed and new ticketless Conversations execute through the same Agent reply function.
3. No direct model CLI, standalone Auto Reply Job or duplicate Ticketless Agent service exists.
4. A human takeover or newer customer message suppresses stale Agent output.
5. Ticketless handoff assignment observes presence, capacity, scope and FIFO.
6. Closing an accepted Conversation releases capacity and assigns the next eligible request.
7. `ticket.create` requires trusted server authorization and is idempotent.
8. Starting or operating a ticketless voice session never creates a Ticket.
9. Tool arguments are schema-validated before handler execution.
10. PostgreSQL migration, complete backend regression, frontend verification, browser journeys, security checks and image smoke pass on one exact immutable Head.

## Retirement rule

Any path that assumes a Ticket is mandatory for text, handoff or voice must be changed at its source or deleted. No duplicate live path, model subprocess, compatibility executor, automatic voice-ticket adapter, manual worker bypass, temporary patch or shadow runtime may remain in the accepted tree.
