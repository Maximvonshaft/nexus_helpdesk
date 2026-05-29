# v1.7.8 Template Block Adaptation and Performance Evidence

## Current conclusion

The previous PRs connected real WebChat, WebCall, and Email API capabilities, but that is not equivalent to full v1.7.8 template block migration.

## P0 performance decision

`livekit-client` must not be imported at top-level by any WebCall route or panel. It must be lazy loaded only when the visitor or operator starts an actual media action.

## Remaining template-block work

- WebChat: verify visual parity for queue, message stream, customer profile, AI suggestions, handoff, session actions.
- WebCall: verify visual parity for call queue, sessions, live console, transcript, notes, customer profile, identity verification, AI suggestions, handoff, session actions.
- Email: verify visual parity for queue, thread, composer, draft/send/audit state.
- Today Workbench: this PR maps `/` to the v1.7.8 `今日工作台 / 我的优先事项` block set with a real `/api/workbench/today` backend view model. It now exposes role tasks, metrics, SLA risk rows, visible entrypoints, Command Center rows, interaction states and source contracts instead of frontend-only fixture counts.

## Next PR

The next PR should continue template-block parity for WebChat, WebCall, or Email visual/runtime blocks, not another performance-only PR.
