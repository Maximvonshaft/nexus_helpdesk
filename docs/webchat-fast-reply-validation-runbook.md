# WebChat Fast Reply Validation Runbook

This runbook validates the current WebChat Fast Reply chain after retiring the legacy OpenClaw direct provider.

## Scope

These checks are local/staging/prod-like safe. They validate:

- `/healthz` and `/readyz` are reachable.
- `/api/webchat/fast-reply` returns an AI-generated reply through Provider Runtime.
- Browser/static bundles do not expose provider URLs, bearer tokens, API keys, or retired OpenClaw gateway credentials.
- `/api/webchat/fast-reply` sustains the configured concurrency target.

## One-Command Release Gate

Run from the repository root after deploying the target environment:

```bash
BASE_URL="http://127.0.0.1:18081" \
CONCURRENCY=25 \
REQUESTS=100 \
P95_MS=5000 \
MAX_MS=8000 \
bash scripts/smoke/webchat_fast_reply_release_gate.sh
```

Expected result:

```text
STATUS=PASS
```

Logs are written under:

```text
./outputs/webchat_fast_reply_release_gate/
```

## Wrapper Steps

1. `/healthz` and `/readyz` probe.
2. Browser/static secret scan.
3. Single fast-reply API contract probe.
4. 25-concurrency smoke by default.

## Wrapper Knobs

```bash
BASE_URL="http://127.0.0.1:18081"
CONCURRENCY=25
REQUESTS=100
P95_MS=5000
MAX_MS=8000
DIST_DIR="webapp/dist"
STATIC_DIR="backend/app/static/webchat"
REPORT_DIR="./outputs/webchat_fast_reply_release_gate"
```

Diagnostic skips:

```bash
SKIP_SECRET_SCAN=true
SKIP_CONCURRENCY_SMOKE=true
```

Skipped results must not be used as production approval evidence.

## Backend Tests

```bash
cd backend
pytest -q \
  tests/test_webchat_fast_reply_output_parser.py \
  tests/test_webchat_fast_reply_api.py \
  tests/test_webchat_widget_fast_mode_static.py \
  tests/test_webchat_fast_runtime_config_static.py \
  tests/test_webchat_fast_ai_provider_router_phase1.py \
  tests/test_provider_runtime_router_fallback_e2e.py
```

## Manual Browser Check

Embed without `data-webchat-mode`:

```html
<script src="https://YOUR_DOMAIN/webchat/widget.js" data-tenant="default" data-channel="website" async></script>
```

Expected:

- Widget defaults to `fast_ai`.
- Sending a message calls `POST /api/webchat/fast-reply`.
- It does not call retired provider endpoints from the browser.
- It does not expose provider URL/token material in browser network requests or source.

## Release Gate

Do not keep fast mode in production unless:

1. CI on `main` is green.
2. `webchat_fast_reply_release_gate.sh` returns `STATUS=PASS` in staging or prod-like staging.
3. Manual browser check confirms fast mode only talks to NexusDesk.
4. Rollback mode `data-webchat-mode="legacy"` is still available for the widget.
