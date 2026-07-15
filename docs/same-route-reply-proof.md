# Same-route Reply Proof

## Definition

A reply is same-route only when it is sent back through the original customer conversation route:

- `channel`
- `recipient`
- `account_id`
- `thread_id`
- adapter/provider identity

For customer messaging, guessing a fallback recipient is not acceptable.

## Preferred Order

1. Use the ticket's explicit outbound route fields when available.
2. Use a channel-native sidecar or provider adapter that can prove the exact account and thread.
3. Fall back to draft/human review when route provenance is incomplete.
4. Do not use the retired ExternalChannel bridge, MCP, Gateway, or CLI path for production sends.

## Route Proof Log

A valid proof must show non-secret fields only:

```json
{
  "channel": "whatsapp",
  "recipient": "+41000000001",
  "account_id": "wa-business-01",
  "thread_id": "customer-thread-001",
  "provider": "native_sidecar",
  "route_provenance": "ticket_link"
}
```

## Must Not Send

The system must not send when:

- `channel` or `recipient` is missing.
- route provenance is ambiguous.
- multiple open tickets match the same recipient and no ticket link exists.
- the customer-visible content policy returns `block`.
- an AI message fails its signed Runtime contract.
- the selected adapter is the retired ExternalChannel bridge.

## Proof Scope

Mock smoke tests prove route preservation and customer-visible policy enforcement. They do not replace live staging validation against the specific native provider or sidecar that will carry customer traffic.
