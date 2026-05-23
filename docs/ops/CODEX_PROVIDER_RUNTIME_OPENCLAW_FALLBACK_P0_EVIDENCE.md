# Codex Provider Runtime OpenClaw Fallback P0 Evidence

Date: 2026-05-23
Branch: `fix/codex-provider-runtime-openclaw-fallback-p0`

## Scope

This PR keeps production default traffic safe while making Codex a controlled provider_runtime primary with OpenClaw as the first fallback.

Required default:

```json
{
  "primary_provider": "codex_app_server",
  "fallback_providers": ["openclaw_responses", "rule_engine"],
  "canary_percent": 0,
  "kill_switch": false
}
```

## Evidence Matrix

| Requirement | Evidence |
| --- | --- |
| Codex success path | `backend/tests/test_provider_runtime_router_fallback_e2e.py::test_codex_success_path_e2e` |
| Codex failure -> OpenClaw fallback | `backend/tests/test_provider_runtime_router_fallback_e2e.py::test_codex_failure_falls_back_to_openclaw_e2e` |
| Kill switch bypasses Codex | `backend/tests/test_provider_runtime_router_fallback_e2e.py::test_kill_switch_bypasses_codex_e2e` |
| Canary 0 bypasses Codex | `backend/tests/test_provider_runtime_router_fallback_e2e.py::test_canary_zero_bypasses_codex_e2e` |
| No raw access/refresh token in result/audit/status | `backend/tests/test_provider_runtime_router_fallback_e2e.py::test_audit_and_result_do_not_expose_raw_oauth_tokens_e2e`, `backend/tests/test_provider_runtime_token_leakage.py`, `backend/tests/test_provider_runtime_codex_oauth_webchat.py::test_provider_runtime_status_credential_summary_has_no_raw_tokens` |
| Admin route can update safe routing controls | `backend/tests/test_admin_provider_runtime_routing_api.py` |
| OpenClaw registered as provider_runtime adapter | `backend/tests/test_provider_runtime_openclaw_fallback_adapter.py` |

## Local Verification

Backend requirements were installed with Python 3.11:

```powershell
py -3.11 -m pip install -r backend\requirements.txt
```

Route smoke command:

```powershell
$env:PYTHONPATH='backend'
py -3.11 -c "from app.main import app; routes=sorted(getattr(route,'path','') for route in app.routes); assert '/api/admin/provider-runtime/status' in routes; assert '/api/admin/provider-runtime/routing/webchat-fast-reply' in routes; print('route_smoke_ok')"
```

Result: `route_smoke_ok`.

Targeted tests:

```powershell
$env:PYTHONPATH='backend'
py -3.11 -m pytest -q backend/tests/test_provider_runtime_openclaw_fallback_adapter.py backend/tests/test_provider_runtime_kill_switch_canary.py backend/tests/test_provider_runtime_router_fallback_e2e.py backend/tests/test_admin_provider_runtime_routing_api.py backend/tests/test_provider_runtime_codex_oauth_webchat.py backend/tests/test_codex_auth_profile_importer.py backend/tests/test_provider_runtime_router.py
```

Result: `25 passed in 1.90s`.

Provider runtime regression:

```powershell
$env:PYTHONPATH='backend'
$providerTests = Get-ChildItem backend\tests -Filter 'test_provider_runtime*.py' | ForEach-Object { $_.FullName }
py -3.11 -m pytest -q @providerTests backend/tests/test_codex_auth_profile_importer.py backend/tests/test_codex_oauth_config.py backend/tests/test_oauth_refresh_concurrency.py backend/tests/test_webchat_fast_reply_provider_runtime.py backend/tests/test_webchat_codex_app_server_canary_observability.py
```

Result: `54 passed, 1 warning in 5.24s`.

Migration head:

```powershell
$env:PYTHONPATH='.'
py -3.11 -m alembic -c alembic.ini heads
```

Result: `20260523_0032 (head)`.

## Production Safety Notes

- No production deployment was performed from local.
- The new migration updates existing default WebChat fast reply routing rows to `0%` Codex canary with OpenClaw fallback first.
- `provider-runtime-gate` now runs the new OpenClaw adapter, canary/kill switch, admin routing, and fallback E2E tests on pull requests.
