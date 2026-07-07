# Manual Staging Smoke Runbook

## Purpose

`manual-staging-smoke` validates a deployed NexusDesk environment before production feature flags are considered.

This is a deployment/runtime gate, not a code-merge gate.

## Scope

The workflow is read-only. It checks:

- `/healthz`
- `/readyz`
- browser/security headers on `/`
- optional WebChat assets
- optional CORS preflight for `/api/webchat/init`
- optional `/metrics` with `STAGING_METRICS_TOKEN`

It does not:

- send customer messages
- create tickets
- execute Speedaf write actions
- call Speedaf directly
- enable production feature flags

## Workflow

Manual workflow name:

```text
manual-staging-smoke
```

Required input:

```text
base_url=https://your-staging-domain
```

Optional inputs:

```text
expected_status=ready
check_webchat_assets=true
check_metrics=false
cors_origin=https://your-public-widget-origin
```

## Metrics

If `check_metrics=true`, configure repository secret:

```text
STAGING_METRICS_TOKEN
```

Do not place metrics tokens in committed files, PR comments, screenshots, or issue text.

## Pass Criteria

A staging smoke pass requires:

- `/healthz` returns `status=ok`
- `/readyz` returns expected status, normally `ready`
- root response includes:
  - `X-Content-Type-Options`
  - `X-Frame-Options`
  - `Referrer-Policy`
  - `Content-Security-Policy`
  - `Permissions-Policy`
- WebChat demo and widget assets are reachable if enabled
- CORS preflight returns `access-control-allow-origin` if `cors_origin` is provided
- `/metrics` returns Prometheus text exposition if enabled

## Production Boundary

Passing this smoke does not authorize Speedaf write flags.

Still required before production enablement:

- Branch protection from Issue #179
- Speedaf read-only UAT smoke
- Operator approval
- Feature flag staged rollout
- Rollback plan
