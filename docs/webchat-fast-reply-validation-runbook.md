# WebChat Fast Reply Validation Runbook

This runbook validates the WebChat AI-only fast reply chain introduced by PR #61, PR #63, PR #64, and PR #69.

## Scope

These checks are local/staging/prod-like safe. They do not access the production database directly.

They validate:

- `/healthz` and `/readyz` are reachable.
- `/api/webchat/fast-reply` returns an AI-generated reply.
- Browser/static bundles do not expose OpenClaw Gateway URLs or tokens.
- OpenClaw `/v1/responses` URL is private-only by hostname/path/DNS classification.
- `/api/webchat/fast-reply` sustains the configured concurrency target.

## Recommended one-command release gate

Run this from the repository root after deploying the target environment.

```bash
BASE_URL="http://127.0.0.1:18081" \
OPENCLAW_RESPONSES_URL="$OPENCLAW_RESPONSES_URL" \
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

The wrapper writes timestamped logs under:

```text
./outputs/webchat_fast_reply_release_gate/
```

### Wrapper steps

The wrapper runs, in order:

1. `/healthz` and `/readyz` probe.
2. Browser/static OpenClaw exposure scan.
3. OpenClaw private URL classification probe.
4. Single fast-reply API contract probe.
5. 25-concurrency smoke by default.

### Wrapper knobs

```bash
BASE_URL="http://127.0.0.1:18081"
OPENCLAW_RESPONSES_URL="http://openclaw-gateway-private:18789/v1/responses"
CONCURRENCY=25
REQUESTS=100
P95_MS=5000
MAX_MS=8000
DIST_DIR="webapp/dist"
STATIC_DIR="backend/app/static/webchat"
REPORT_DIR="./outputs/webchat_fast_reply_release_gate"
```

Optional emergency skips, for diagnostics only:

```bash
SKIP_SECRET_SCAN=true
SKIP_OPENCLAW_PROBE=true
SKIP_CONCURRENCY_SMOKE=true
```

Do not use skipped results as production approval evidence.

## Individual checks

### 1. Targeted backend/widget tests

```bash
cd backend
pytest -q tests/test_webchat_fast_reply_output_parser.py
pytest -q tests/test_webchat_fast_reply_api.py
pytest -q tests/test_webchat_openclaw_responses_client.py
pytest -q tests/test_webchat_widget_fast_mode_static.py
pytest -q tests/test_webchat_fast_runtime_config_static.py
```

### 2. Browser/static secret scan

```bash
python scripts/smoke/browser_bundle_secret_scan.py \
  --dist webapp/dist \
  --static backend/app/static/webchat
```

Expected: `finding_count` is `0`.

### 3. OpenClaw private exposure probe

```bash
python scripts/smoke/openclaw_gateway_private_exposure_probe.py \
  --responses-url "$OPENCLAW_RESPONSES_URL"
```

Expected: `pass` is `true`.

This probe checks URL scheme/path, private host naming, and DNS resolution. It fails if the host resolves to a public IP.

### 4. Fast reply API contract probe

The release gate wrapper sends one real public API request to:

```text
POST /api/webchat/fast-reply
```

Expected:

- HTTP 2xx.
- `ok=true`.
- `ai_generated=true`.
- `reply` is non-empty.

If this fails, check NexusDesk environment variables and private OpenClaw Gateway readiness before running concurrency smoke.

### 5. WebChat fast reply concurrency smoke

Run against a local or staging NexusDesk instance with private OpenClaw configured:

```bash
python scripts/smoke/webchat_fast_reply_concurrency_smoke.py \
  --base-url http://127.0.0.1:18081 \
  --concurrency 25 \
  --requests 100 \
  --p95-ms 5000 \
  --max-ms 8000
```

Pass criteria:

- success rate >= 95%
- P95 <= 5000ms
- max <= 8000ms

## Manual browser check

Embed without `data-webchat-mode`:

```html
<script src="https://YOUR_DOMAIN/webchat/widget.js" data-tenant="default" data-channel="website" async></script>
```

Expected:

- Widget defaults to `fast_ai`.
- Sending a message calls `POST /api/webchat/fast-reply`.
- It does not call `/api/webchat/init`.
- It does not poll `/api/webchat/conversations/.../messages`.
- It does not expose OpenClaw URL/token in browser network requests or source.

Rollback check:

```html
<script src="https://YOUR_DOMAIN/webchat/widget.js" data-webchat-mode="legacy" async></script>
```

Expected:

- Legacy mode uses `/api/webchat/init`, conversation messages, and polling.

## Release gate

Do not enable or keep fast mode in production until:

1. PR #61, PR #63, PR #64, and PR #69 are merged.
2. CI on main is green.
3. `webchat_fast_reply_release_gate.sh` returns `STATUS=PASS` in staging or prod-like staging.
4. Manual browser check confirms fast mode does not call the legacy init/messages/polling chain.
5. Rollback mode `data-webchat-mode="legacy"` is still available.
