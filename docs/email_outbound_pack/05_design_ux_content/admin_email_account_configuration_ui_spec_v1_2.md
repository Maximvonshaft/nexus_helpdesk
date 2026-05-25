# Admin Email Account Configuration UI Spec v1.2

## Goal

Allow an authorized admin or operations manager to configure Email sending from the backend/admin UI without asking engineers to edit code.

## Current UI gap

The current Channel Accounts page lists WhatsApp, Telegram, and SMS provider options. v1.2 must add Email governance without creating fake fields that backend cannot persist.

## Preferred navigation

Add one of the following:

1. `/accounts` with tabs: Chat Channels / Email.
2. `/email-accounts` linked from `/accounts`.

Preferred implementation: `/email-accounts` or an Email tab to keep Email-specific verification and test-send flows clean.

## Page layout

### Header

Title: `Email sending accounts`  
Subtitle: `Configure customer support Email sending accounts, verification, health checks, and test sends.`

### Metrics strip

- Total Email accounts.
- Ready accounts.
- Accounts missing verification.
- Failed health checks.
- Bounces in last 24h.
- Suppressed recipients.

### Left panel: account list

Each account card shows:

- Display name.
- From email.
- Market/global.
- Identity status badge.
- Health status badge.
- Active/inactive badge.
- Last checked.

### Right panel: editor

Fields:

- Display name.
- Market.
- From email.
- From name.
- Reply-To.
- Return-Path.
- SES region.
- SES configuration set.
- Secret reference.
- Priority.
- Fallback account.
- Active toggle.

### Readiness checklist

Show exact missing keys returned by backend:

- Runtime disabled.
- Provider disabled.
- Secret missing.
- Identity not verified.
- Customer recipient invalid.
- Webhook verification disabled.
- Inbound route not configured.

### Action buttons

- Save.
- Check verification.
- Run health check.
- Send test email.
- Disable account.
- View delivery events.
- View suppression list.

## Error states

- Save failed: show field-level errors.
- Verification failed: show provider-safe diagnostic, not secrets.
- Test send failed: show provider error class and request id.
- Account not ready: show missing items and next action.

## Safety

- Never show raw secret value.
- Never allow plaintext AWS secret input in normal UI.
- Do not show Email as ready unless backend readiness says ready.
