# PR Template — Email Outbound Production

## Summary

Implement production-grade Email outbound channel.

## Scope

- [ ] Data model/migration
- [ ] Capability registry
- [ ] Email adapter
- [ ] SES provider
- [ ] Delivery events
- [ ] Inbound replies
- [ ] Frontend UI
- [ ] Docs/runbook

## Risk

Risk level: R4

## Test evidence

Paste command outputs:
- [ ] Backend tests
- [ ] Frontend typecheck/build/tests
- [ ] Migration upgrade
- [ ] Staging smoke

## Security checklist

- [ ] No secrets in DB.
- [ ] No secrets in logs.
- [ ] Header injection blocked.
- [ ] Suppression enforced.
- [ ] Webhook authenticated.
- [ ] HTML sanitized.

## Rollback

Config rollback:
```bash
OUTBOUND_EMAIL_ENABLED=false
EMAIL_PROVIDER=disabled
```

Full outbound rollback:
```bash
ENABLE_OUTBOUND_DISPATCH=false
OUTBOUND_PROVIDER=disabled
```
