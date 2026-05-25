# Data Contract Guardrails

## Canonical queue row

`ticket_outbound_messages` remains canonical for outbound lifecycle:
- pending
- processing
- sent
- failed
- dead
- draft

## Email metadata

Store email-specific fields in a linked table:

```text
email_outbound_metadata.outbound_message_id -> ticket_outbound_messages.id
```

Required fields:
- subject
- from_email
- from_name
- to_email
- cc_json
- bcc_json
- reply_to_email
- return_path_email
- message_id_header
- in_reply_to
- references_header
- text_body
- html_body_sanitized
- provider
- provider_message_id
- provider_raw_json

## Delivery events

Store provider events in `email_delivery_events`.

Deduplication key:
- provider + provider_message_id + event_type + occurred_at
or provider event id if available.

## Inbound messages

Store inbound emails in `email_inbound_messages`.

Linking priority:
1. `X-NexusDesk-Ticket-ID`
2. plus addressing `support+ticket-{id}@domain`
3. `In-Reply-To` / `References`
4. provider metadata
5. fallback from_email + subject similarity, only if one safe match exists

## Suppression

A hard bounce or complaint must create/update `email_suppression_entries`.

Suppressed recipients must be blocked by capability/send validation.
