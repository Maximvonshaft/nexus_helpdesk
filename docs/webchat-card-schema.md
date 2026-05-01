# WebChat Card Schema

## Message contract

Structured WebChat messages extend the existing text model. `body` remains the compatibility field.

```json
{
  "id": 123,
  "direction": "system",
  "body": "How can we help you?",
  "body_text": "Choose one option below.",
  "message_type": "card",
  "payload_json": {},
  "metadata_json": {},
  "delivery_status": "sent",
  "action_status": "pending"
}
```

Allowed `message_type` values:

- `text`
- `system`
- `card`
- `action`
- `attachment`

## Card payload

```json
{
  "card_id": "card_xxx",
  "card_type": "quick_replies",
  "version": 1,
  "title": "How can we help you?",
  "body": "Choose one option below.",
  "actions": [
    {
      "id": "track_parcel",
      "label": "Track my parcel",
      "value": "track_parcel",
      "action_type": "quick_reply",
      "payload": {"intent": "tracking"}
    }
  ],
  "metadata": {
    "intent": "tracking",
    "generated_by": "system",
    "requires_audit": true
  }
}
```

Allowed `card_type` values:

- `quick_replies`
- `tracking_status`
- `address_confirmation`
- `reschedule_picker`
- `photo_upload_request`
- `handoff`
- `csat`

Fully rendered in this release:

- `quick_replies`
- `handoff`

Schema-safe/fallback-first in this release:

- `tracking_status`
- `address_confirmation`
- `reschedule_picker`
- `photo_upload_request`
- `csat`

## Action submit contract

```json
{
  "message_id": 123,
  "card_id": "card_xxx",
  "action_id": "track_parcel",
  "action_type": "quick_reply",
  "payload": {"intent": "tracking"}
}
```

Allowed `action_type` values:

- `quick_reply`
- `handoff_request`
- `address_confirm`
- `address_edit`
- `address_cancel`
- `reschedule_submit`
- `photo_upload_submit`
- `csat_submit`

## Validation rules

- `card_type` must be allowlisted.
- `message_type` must be allowlisted.
- `title`, `body`, and action labels have length limits.
- HTML/JS/style/iframe/object/embed/link/meta/svg/math markup is rejected.
- `javascript:` and unsafe data URL patterns are rejected.
- URL fields must use `https://`.
- Action IDs must use a safe string format.
- Oversized payloads are rejected.

## Renderer rule

The client widget uses a card renderer registry and DOM `textContent`. Card payload is data only; it is never executed as HTML, JavaScript, CSS, iframe content, or template code.
