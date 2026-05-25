# Feature Flag Rollout Strategy

## Flags

| Flag | Purpose |
|---|---|
| `OUTBOUND_EMAIL_ENABLED` | Email channel kill switch |
| `EMAIL_PROVIDER` | Provider selector |
| `EMAIL_DELIVERY_EVENTS_ENABLED` | Delivery event ingestion |
| `EMAIL_INBOUND_ENABLED` | Inbound reply ingestion |
| `EMAIL_ALLOW_CLICK_OPEN_TRACKING` | Tracking disabled by default |

## Rollout

1. Code merged with Email disabled.
2. Staging account configured.
3. Staging smoke.
4. Production deploy with Email disabled.
5. Enable for internal test mailbox.
6. Enable for one market/team.
7. Monitor for 24–72 hours.
8. Wider rollout.
