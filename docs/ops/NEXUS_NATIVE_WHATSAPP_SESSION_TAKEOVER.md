# Nexus Native WhatsApp Session Takeover Runbook

Date: 2026-07-01

Scope: controlled candidate-only validation for moving an already-linked WhatsApp Web session from the legacy runtime to the Nexus native WhatsApp sidecar.

## Current Facts

- Fresh QR and pairing-code attempts can reach `partial`, but WhatsApp returns `disconnect_loggedOut`.
- The legacy runtime credential directory contains a complete Baileys multi-file auth session for `+41798559737`.
- Importing that credential directory into the Nexus candidate sidecar produces `session_state=linked`.
- The imported session is then rejected with `disconnect_connectionReplaced`, which indicates the same linked-device credential is still active in the legacy runtime.
- Nexus candidate backend status callbacks are now proven healthy for the test channel account: `/api/integrations/whatsapp/native/status` returns `200`.

## Impact

This takeover test can briefly interrupt the legacy support console channel because the legacy gateway on `127.0.0.1:18789` owns the active WhatsApp client.

Do not run this during active customer handling unless the operator accepts that short interruption.

## Candidate Account

Use the existing Nexus test account:

```text
wa-test-41798559737
```

The sidecar `WA_SIDECAR_AUTO_START_ACCOUNTS` must match an existing `ChannelAccount.account_id`; otherwise backend status callbacks return `404`.

## Preflight

1. Confirm the candidate app is healthy.
2. Confirm the candidate sidecar is healthy.
3. Confirm the target account exists in `ChannelAccount`.
4. Backup the candidate sidecar account directory before importing credentials.
5. Never copy credential files into Git, docs artifacts, CI logs, or screenshots.

## Controlled Takeover

1. Stop only the candidate sidecar.
2. Backup the candidate test account session directory.
3. Copy the legacy runtime Baileys credential directory into the candidate account session directory.
4. Start the candidate sidecar and verify `session_state=linked`.
5. Pause the legacy gateway for the approved window.
6. Restart the candidate sidecar.
7. Poll for `status=connected`.
8. If connected, run inbound and outbound smoke with explicit send approval.
9. If not connected within the window, restart the legacy gateway and stop the candidate sidecar.

## Rollback

1. Stop candidate sidecar.
2. Restore the previous candidate test account session backup or leave the imported session in place but stopped.
3. Restart the legacy gateway with the original command and verify `/healthz`.
4. Confirm candidate public app and worker health remain unchanged.

## Success Criteria

- Candidate sidecar reports `status=connected` and `session_state=linked`.
- Nexus backend records the WhatsApp account health as `healthy`.
- One inbound message creates exactly one ticket/conversation/message.
- One explicitly approved outbound smoke sends through the native sidecar and records a delivery callback.

## Abort Criteria

- WhatsApp returns `disconnect_loggedOut` after imported credentials.
- WhatsApp returns repeated `connectionReplaced` after the legacy gateway is paused.
- Candidate backend callback status is not `200`.
- Legacy gateway cannot be restarted immediately during rollback.
