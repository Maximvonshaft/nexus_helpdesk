# Admin SOP — Configure Email Channel in Backend UI v1.2

## Before admin starts

DevOps/provider owner must prepare:

1. AWS SES identity/domain.
2. SPF/DKIM/DMARC records.
3. Optional inbound MX records.
4. SES configuration set for events.
5. Secret reference available to NexusDesk runtime.
6. Email feature flags still fail-closed until smoke test.

## Admin setup steps

1. Open NexusDesk admin backend.
2. Go to Email sending accounts.
3. Click Create Email Account.
4. Select market or global.
5. Enter display name.
6. Enter From email and From name.
7. Enter Reply-To and Return-Path.
8. Select SES region.
9. Enter configuration set.
10. Enter secret reference name.
11. Save.
12. Click Check verification.
13. Click Health check.
14. If ready, click Send test email to internal test recipient.
15. Enable account.
16. Ask support agent to test on a controlled ticket.

## If not ready

Use the readiness checklist:

- Secret missing: DevOps must provision runtime secret.
- Identity not verified: provider/DNS owner must complete SES verification.
- DNS missing: DNS owner must publish records.
- Webhook disabled: DevOps must enable delivery event route.
- Inbound disabled: email replies may not return to tickets yet.

## Agent handoff

After test send succeeds, notify agents that Email is available only on tickets with valid customer email and ready market/global account.
