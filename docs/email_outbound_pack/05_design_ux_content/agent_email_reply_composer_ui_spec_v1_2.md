# Agent Email Reply Composer UI Spec v1.2

## Goal

Allow support agents to send Email replies from the ticket workspace with the same operational confidence as other channels.

## Required fields when channel=email

- From: read-only account display.
- To: default customer email, editable only with permission.
- Subject: required, prefilled.
- Body: required.
- CC/BCC: advanced collapsed section.
- External send confirmation checkbox.

## Prefill logic

Subject priority:

1. Existing email thread subject from `EmailOutboundMetadata` or inbound message.
2. `Re: [ticket_no] {ticket.title}`.

To priority:

1. `payload.to_email` if explicitly supplied and user has permission.
2. `ticket.preferred_reply_contact` if valid email.
3. `ticket.customer.email`.

From priority:

1. Ticket channel account.
2. Market email account.
3. Global email account.

## Disabled behavior

If Email capability is not sendable:

- Show Email in disabled state or hide based on product preference.
- Display missing reasons from backend.
- Do not let agent force-send.

## Send success behavior

After POST `/api/tickets/{id}/outbound/send`:

- Toast: `Email queued for sending`.
- Refresh ticket timeline.
- Refresh queue summary if visible.
- Do not say `delivered` until provider delivery event arrives.

## Timeline cards

Render Email-specific cards for:

- queued.
- provider accepted.
- delivered.
- bounced.
- complaint.
- inbound reply received.
- suppression block.

## Regression rule

Existing WhatsApp, Telegram, SMS, and WebChat reply flows must not change except for safe shared capability rendering.
