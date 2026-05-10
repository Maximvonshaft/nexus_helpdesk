# WebChat Fast Reply Validation Runbook

This runbook validates the WebChat AI-only fast reply chain introduced by PR #61 and switched on by PR #63.

## Scope

These checks are local/staging-safe and must not access production databases.

They validate:

- `/api/webchat/fast-reply` concurrency behavior.
- Browser/static bundles do not expose OpenClaw Gateway URLs or tokens.
- OpenClaw `/v1/responses` URL is private-only by hostname/path/DNS classification.

## 1. Targeted backend/widget tests

```bash
cd backend
pytest -q tests/test_webchat_fast_reply_output_parser.py
pytest -q tests/test_webchat_fast_reply_api.py
pytest -q tests/test_webchat_openclaw_responses_client.py
pytest -q tests/test_webchat_widget_fast_mode_static.py
```

## 2. Browser/static secret scan

```bash
python scripts/smoke/browser_bundle_secret_scan.py \
  --dist webapp/dist \
  --static backend/app/static/webchat
```

Expected: `finding_count` is `0`.

## 3. OpenClaw private exposure probe

```bash
python scripts/smoke/openclaw_gateway_private_exposure_probe.py \
  --responses-url "$OPENCLAW_RESPONSES_URL"
```

Expected: `pass` is `true`.

This probe checks URL scheme/path, private host naming, and DNS resolution. It fails if the host resolves to a public IP.

## 4. WebChat fast reply concurrency smoke

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

## 5. Manual browser check

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

Do not enable fast mode in production until:

1. PR #61 is merged.
2. PR #63 is merged or rebased onto main and merged.
3. Targeted backend/widget tests pass.
4. Secret scan returns zero findings.
5. Gateway private exposure probe passes.
6. 25-concurrency smoke passes in staging or prod-like staging.
