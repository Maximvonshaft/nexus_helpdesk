# Nexus Generic Agent Skill Runtime

## Decision

Nexus core is a generic Agent execution scaffold. Business capabilities are added through Skills and Tools. The core runtime must not contain logistics-, knowledge-, finance-, HR-, or reporting-specific routing, fact gates, keyword detectors, repair branches, or fallback logic.

## Canonical flow

1. Build the Agent context: persona, conversation, selected Skills, selected Tool contracts, and prior Tool observations.
2. Ask the model for one `nexus.agent_turn.v1` object.
3. A final turn contains a customer reply and no Tool calls.
4. A Tool turn contains Tool calls and no customer reply.
5. Validate Tool registration, input schema, availability, permission, confirmation, and write-risk policy.
6. Execute through the canonical Tool Executor.
7. Return bounded, redacted Tool observations to the model.
8. Repeat for a bounded number of rounds.
9. Always emit one customer-visible final response, including a deterministic fallback when the model or Tool infrastructure is unavailable.

## Ownership boundaries

### Agent Runtime

- bounded model/Tool loop
- canonical Agent-turn schema
- Tool selection exposure
- Tool observation injection
- timeout and round limits
- customer-visible terminal response
- provider health and audit

### Skill Registry

- when a capability applies
- which Tools may be used
- task-specific instructions
- non-fabrication and failure behavior expressed for that capability

### Tool Contract and Executor

- Tool name and description
- JSON input schema
- permissions and confirmation
- idempotency and risk policy
- execution handler
- safe result projection
- audit

### Tool implementation

- domain API/MCP integration
- domain payload normalization
- domain-specific redaction
- domain result semantics

## Prohibited architecture

The following must not return to the Runtime core:

- keyword lists that decide whether model text is factually allowed
- `intent == <business-domain>` output blocking
- pre-model domain API calls selected by application keyword heuristics
- business-specific contract repair loops
- parallel Tool registries or parallel Tool executors
- silent failure after a customer message has been accepted

## Extension rule

A new Agent capability adds a Skill definition and, when required, a Tool contract and handler. It does not add a new branch to the Runtime loop.
