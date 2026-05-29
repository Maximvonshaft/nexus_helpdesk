# v1.7.8 Template Block Adaptation and Performance Evidence

## Current conclusion

The previous PRs connected real WebChat, WebCall, and Email API capabilities, but that is not equivalent to full v1.7.8 template block migration.

## P0 performance decision

`livekit-client` must not be imported at top-level by any WebCall route or panel. It must be lazy loaded only when the visitor or operator starts an actual media action.

## Remaining template-block work

- WebChat: verify visual parity for queue, message stream, customer profile, AI suggestions, handoff, session actions.
- WebCall: verify visual parity for call queue, sessions, live console, transcript, notes, customer profile, identity verification, AI suggestions, handoff, session actions.
- Email: verify visual parity for queue, thread, composer, draft/send/audit state. Existing and newly uploaded external ticket attachments are selectable from the Email composer and are bound to outbound draft/send records, SMTP MIME dispatch, and timeline payload readback. Provider status, retry/failure evidence, `runtime.manage` gated dead-message requeue, and outbound mailbox thread/message identity are now visible in the Email timeline. SMTP dispatch writes `Message-ID`, `In-Reply-To`, and `References` from the stored mailbox identity.
- Today Workbench: `/` now consumes `/api/lite/today-workbench` for the v1.7.8 Role Home blocks: role tasks, real metrics, SLA priority rows, interaction-state closure and command center actions. Remaining work is visual parity polish against the template screenshots and broader 33-screen registry migration.
- Control Tower / Governance: `/control-tower` now consumes `/api/lite/control-tower` for KPI/tower, manager action queue, team workload, channel health, bulletin impact, RBAC/governance lanes and template closure status. Remaining work is QA/Training Loop and other visible Operations/Engineering handoff template screens.

## Next PR

The next PR should continue template-block parity, with QA/Training Loop or remaining Operations governance screens as the next likely cut.
