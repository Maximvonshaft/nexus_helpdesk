# OpenClaw Integration Runbook

OpenClaw inbound auto-sync and outbound dispatch are separate operational paths.

Inbound path:

- discovers conversations
- reads messages
- links or creates tickets
- persists transcript messages
- records unresolved events when mapping fails

Outbound path:

- remains disabled by default
- must pass provider-level dispatch gates
- must not be enabled by this production audit closure

Required defaults:

- ENABLE_OUTBOUND_DISPATCH=false
- OUTBOUND_PROVIDER=disabled
- OPENCLAW_CLI_FALLBACK_ENABLED=false

Do not change PR 21 inbound auto-sync behavior during this closure. The only acceptable changes in this phase are safety gates, tests, documentation, and observability that do not alter the inbound sync main flow.

Operational validation:

- healthz returns ok
- readyz returns database ok
- OpenClaw runtime health is visible in admin runtime surfaces
- outbound disabled tests pass
