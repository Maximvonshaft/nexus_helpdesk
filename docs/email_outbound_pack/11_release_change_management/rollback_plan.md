# Rollback Plan

## Preferred rollback: Email-only disable

Set:

```bash
OUTBOUND_EMAIL_ENABLED=false
EMAIL_PROVIDER=disabled
EMAIL_DELIVERY_EVENTS_ENABLED=false
EMAIL_INBOUND_ENABLED=false
```

Then restart app/worker according to deployment method.

Expected:
- Email capability becomes not sendable.
- Existing pending Email rows are not dispatched.
- Existing non-email outbound channels continue unaffected.

## Full outbound rollback

If broader outbound risk exists:

```bash
ENABLE_OUTBOUND_DISPATCH=false
OUTBOUND_PROVIDER=disabled
```

Expected:
- All external outbound dispatch stops.
- WebChat local-only behavior remains separate if implemented as local only.

## Database rollback

Do not drop tables in emergency rollback unless release owner approves.
Disable feature first. Preserve evidence for audit.

## Rollback verification

1. Capability API shows Email not ready.
2. Worker logs show no email provider dispatch.
3. No new provider message ids are created.
4. Pending Email queue is stable.
5. Support team notified.

## Rollback owner

Release owner + backend owner.

## v1.1 rollback invariant

Email-only rollback is not allowed to damage the queue.

After setting:

```bash
OUTBOUND_EMAIL_ENABLED=false
EMAIL_PROVIDER=disabled
```

the implementation must guarantee:

```text
pending Email rows remain pending or paused
processing Email rows are released back to pending/paused
retry_count is not incremented because of feature disable
MessageStatus.dead is not used for feature disable
non-Email outbound continues if ENABLE_OUTBOUND_DISPATCH=true and OUTBOUND_PROVIDER=openclaw
```

## Required rollback test

Run a worker-once smoke with this setup:

```text
ENABLE_OUTBOUND_DISPATCH=true
OUTBOUND_PROVIDER=openclaw
OUTBOUND_EMAIL_ENABLED=false
EMAIL_PROVIDER=disabled
```

Expected evidence:

```text
pending_email_before == pending_email_after
email_provider_calls == 0
email_dead_delta == 0
whatsapp_or_other_non_email_path_unaffected == true
```

If these assertions are not available in automated tests, the release is not ready.
