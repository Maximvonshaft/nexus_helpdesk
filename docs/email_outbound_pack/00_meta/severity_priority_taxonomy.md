# Severity and Priority Taxonomy

## Severity

| Severity | Meaning | Example |
|---|---|---|
| S0 | Unsafe external send / data leak | Email sent to wrong recipient or secret logged. |
| S1 | Customer support channel outage | Email cannot send for all agents. |
| S2 | Partial workflow failure | Bounce events not displayed; inbound reply linking degraded. |
| S3 | UI/UX issue | Email option visible but disabled reason unclear. |
| S4 | Documentation/cleanup | Runbook typo. |

## Priority

| Priority | Rule |
|---|---|
| P0 | S0/S1 or any duplicate/misrouted customer email. |
| P1 | Production blocker before enablement. |
| P2 | Required for full rollout but not initial smoke. |
| P3 | Nice-to-have after production stabilization. |
