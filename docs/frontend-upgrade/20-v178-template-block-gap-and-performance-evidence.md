# v1.7.8 Template Block Adaptation and Performance Evidence

## Current conclusion

The previous PRs connected real WebChat, WebCall, and Email API capabilities, but that is not equivalent to full v1.7.8 template block migration.

## P0 performance decision

`livekit-client` must not be imported at top-level by any WebCall route or panel. It must be lazy loaded only when the visitor or operator starts an actual media action.

## Remaining template-block work

- WebChat: verify visual parity for queue, message stream, customer profile, AI suggestions, handoff, session actions.
- WebCall: verify visual parity for call queue, sessions, live console, transcript, notes, customer profile, identity verification, AI suggestions, handoff, session actions.
- Email: verify visual parity for queue, thread, composer, draft/send/audit state. Existing and newly uploaded external ticket attachments are selectable from the Email composer and are bound to outbound draft/send records, SMTP MIME dispatch, and timeline payload readback.
- Today Workbench: compare `/` against v1.7.8 Today Workbench blocks.

## Next PR

The next PR should be a template-block parity PR, not a performance PR.
