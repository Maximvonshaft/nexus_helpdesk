# Same-route Reply Proof

## Definition

A reply is same-route only when it is sent back through the original customer conversation route:

- `session_key`
- `channel`
- `recipient`
- `accountId`
- `threadId`

For customer messaging, guessing a fallback recipient is not acceptable.

## Preferred order

1. Use an existing `OpenClawConversationLink.session_key` with route metadata.
2. Use MCP/Gateway same-route send where route can be resolved from the session.
3. Use bridge dispatch only when the full target route is explicit and logged.
4. Use CLI fallback only as a recovery path with strict route proof.

## Route proof log

A valid proof must show non-secret fields only:

```json
{
  "session_key": "mock-session-001",
  "channel": "whatsapp",
  "recipient": "+41000000001",
  "account_id": "mock-wa-account",
  "thread_id": "mock-thread-001",
  "provider": "openclaw_mcp"
}
```

## Must not send

The system must not send when:

- `session_key` is missing and no explicit target route exists.
- `channel` or `recipient` is missing.
- route provenance is ambiguous.
- multiple open tickets match the same recipient and no ticket link exists.
- the outbound safety gate returns `block`.
- AI output requires review and has not been approved.

## Round A proof

`smoke_e2e_same_route_reply.sh` starts the deterministic OpenClaw mock server and verifies that `messages_send` preserves `channel`, `recipient`, `accountId`, and `threadId`. It also asserts that missing route fields are rejected.

## Remaining live validation

Mock proof does not replace live OpenClaw Gateway/MCP validation. Before customer production enablement, run same-route reply tests on a staging OpenClaw account and verify the customer receives the message in the original channel/thread only.
