# WebChat Operator Guide

## Entry points

- Visitor demo: `/webchat/demo.html`
- Customer embed: `/webchat/widget.js`
- Agent/admin inbox: `/webchat`

Visitors do not log in. Agents must log in to NexusDesk.

## What agents see

The WebChat admin inbox shows:

- visitor text messages
- system/AI acknowledgement messages
- structured cards
- submitted customer actions
- handoff requests
- required action / conversation state
- action audit payloads

## Reply policy

Agent replies are visible in the visitor widget. They go through the outbound safety gate. If the reply contains logistics facts, the agent should tick `has_fact_evidence` only after checking the source system or reliable evidence.

If the safety gate requires review, use `confirm_review` only after human review.

## Action handling

When a visitor clicks a quick reply, the action is recorded in `webchat_card_actions`, a WebChat action message is added to the thread, and a ticket event is written.

When a visitor requests handoff, the ticket is marked for human review and `required_action` is populated.

## Local delivery semantics

WebChat records in `ticket_outbound_messages` are local-only when their provider status is one of:

- `webchat_delivered`
- `webchat_safe_ack_delivered`
- `webchat_ai_safe_fallback`
- `webchat_card_delivered`
- `webchat_handoff_ack_delivered`

These do not mean WhatsApp/Telegram/SMS/Email messages were sent.

## Testing

```bash
cd backend
python3 -m compileall app scripts
pytest -q

cd ../webapp
npm run typecheck
npm run build
npm run lint

BASE_URL=http://127.0.0.1:8080 bash scripts/smoke/smoke_webchat_cards.sh
```

For admin thread smoke, pass a valid admin JWT:

```bash
ADMIN_TOKEN='<jwt>' BASE_URL=http://127.0.0.1:8080 bash scripts/smoke/smoke_webchat_cards.sh
```

## Rollback

1. Revert the feature commit.
2. Rebuild app and worker images.
3. If the migration has already been applied, run Alembic downgrade only after verifying there are no required WebChat card/action rows to keep.
4. Keep `ENABLE_OUTBOUND_DISPATCH=false` and `OUTBOUND_PROVIDER=disabled`.

## Current limits

- No WebSocket/SSE; polling is optimized but still polling.
- Photo upload is not enabled.
- Full shipment-status cards require trusted tracking tool/database evidence before activation.
- Address/reschedule workflows require business-system integration before customer-visible success states.
