# Email Outbound Production PR

## Summary

## What changed?

- [ ] Capability registry
- [ ] Data model / migration
- [ ] Email adapter
- [ ] SES provider
- [ ] Delivery events
- [ ] Inbound replies
- [ ] Frontend UI
- [ ] Docs/runbook

## Why?

Email is currently blocked as `experimental_not_ready`; this PR closes the production support email channel gap.

## Risk Level

R4 — customer-facing, PII, external provider, delivery/inbound workflow.

## Test Evidence

| Command | Result |
|---|---|
| `python -m pytest backend/tests/test_email_channel_capabilities.py` | |
| `python -m pytest backend/tests/test_email_outbound_adapter.py` | |
| `python -m pytest backend/tests/test_email_delivery_events.py` | |
| `python -m pytest backend/tests/test_email_inbound_parser.py` | |
| `python -m pytest backend/tests/test_email_security.py` | |
| `python -m pytest backend/tests/test_production_dispatch_gates.py backend/tests/test_outbound_message_semantics.py` | |
| `npm --prefix webapp run typecheck` | |
| `npm --prefix webapp run build` | |

## Security checklist

- [ ] No secrets in DB.
- [ ] No secrets in logs.
- [ ] Webhooks authenticated.
- [ ] Header injection blocked.
- [ ] Suppression enforced.
- [ ] HTML sanitized.
- [ ] Email disabled by default.

## Rollback

Email-only:
```bash
OUTBOUND_EMAIL_ENABLED=false
EMAIL_PROVIDER=disabled
```

Full external outbound:
```bash
ENABLE_OUTBOUND_DISPATCH=false
OUTBOUND_PROVIDER=disabled
```
