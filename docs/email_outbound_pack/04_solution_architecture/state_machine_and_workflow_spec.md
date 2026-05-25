# State Machine and Workflow Spec

## Outbound message lifecycle

```text
draft -> pending -> processing -> sent
                     |-> pending retry
                     |-> dead
```

Email must reuse this lifecycle.

## Email delivery lifecycle

Separate provider delivery events:

```text
accepted/sent -> delivered
              -> bounced
              -> complained
              -> delayed
              -> rejected
```

Do not confuse `TicketOutboundMessage.status=sent` with final mailbox delivery. `sent` means provider accepted the send attempt.

## Ticket conversation state

On successful provider acceptance:
- Set ticket conversation state to `waiting_customer`, same as current outbound success behavior.

On inbound customer reply:
- If previous state was `waiting_customer` or `replied_to_customer`, set `reopened_by_customer`.
- Otherwise set/keep active customer-response state according to existing conversation state rules.

## Suppression workflow

```text
bounce/complaint event
  -> upsert email_suppression_entries
  -> future capability/send blocks recipient
  -> operator sees suppression reason
```

Manual suppression removal is out of V1 unless admin capability exists and is explicitly approved.
