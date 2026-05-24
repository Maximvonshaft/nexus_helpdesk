from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib import error, request


ROOT = Path(__file__).resolve().parents[2]


def _load_adapter(
    monkeypatch,
    tmp_path,
    *,
    enabled: bool = True,
    model: str = "openai/gpt-5.5",
    transport: str = "local",
):
    monkeypatch.setenv("OPENCLAW_CODEX_RUNTIME_ENABLED", "true" if enabled else "false")
    monkeypatch.setenv("OPENCLAW_CODEX_CLI", "openclaw")
    monkeypatch.setenv("OPENCLAW_CODEX_AUTH_PROVIDER", "openai-codex")
    monkeypatch.setenv("OPENCLAW_CODEX_PLUGIN_PACKAGE", "@openclaw/codex")
    monkeypatch.setenv("OPENCLAW_CODEX_REQUIRE_PLUGIN", "true")
    monkeypatch.setenv("OPENCLAW_CODEX_MODEL", model)
    monkeypatch.setenv("OPENCLAW_CODEX_INFER_TRANSPORT", transport)
    monkeypatch.setenv("OPENCLAW_CODEX_READY_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("OPENCLAW_CODEX_READY_SMOKE_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("OPENCLAW_CODEX_READY_SMOKE_TTL_SECONDS", "0")
    monkeypatch.setenv("OPENCLAW_CODEX_REPLY_TIMEOUT_SECONDS", "1")
    spec = importlib.util.spec_from_file_location(
        f"codex_openclaw_codex_harness_adapter_test_{id(tmp_path)}",
        ROOT / "deploy" / "codex_openclaw_codex_harness_adapter.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _read_json(url: str) -> tuple[int, dict]:
    try:
        with request.urlopen(url, timeout=2) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _post_json(url: str, payload: dict, *, token: str | None = "oauth-access") -> tuple[int, dict]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST", headers=headers)
    try:
        with request.urlopen(req, timeout=2) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _reply_payload(body: str = "Where is my parcel?") -> dict:
    return {
        "body": body,
        "messages": [{"role": "user", "content": body}],
        "contract": "speedaf_webchat_fast_reply_v1",
        "tracking_fact_summary": None,
        "tracking_fact_evidence_present": False,
        "chatgptAccountId": "acct-1",
        "chatgptPlanType": "plus",
        "response_contract": {
            "reply": "string",
            "intent": "greeting|tracking|tracking_missing_number|tracking_unresolved|complaint|address_change|handoff|other",
            "tracking_number": "string|null",
            "handoff_required": "boolean",
            "handoff_reason": "string|null",
            "recommended_agent_action": "string|null",
        },
    }


def _strict_reply(reply: str = "Please share your tracking number so I can check your parcel status.") -> dict:
    return {
        "reply": reply,
        "intent": "tracking_missing_number",
        "tracking_number": None,
        "handoff_required": False,
        "handoff_reason": None,
        "recommended_agent_action": None,
    }


def _completed(args: list[str], stdout: str = "{}") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")


def _infer_envelope(reply: dict) -> str:
    return json.dumps({"ok": True, "outputs": [{"text": json.dumps(reply)}]})


def test_openclaw_codex_adapter_healthz_is_liveness(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path, enabled=False, model="")
    server = adapter.ThreadingHTTPServer(("127.0.0.1", 0), adapter.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _read_json(f"http://127.0.0.1:{server.server_address[1]}/healthz")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 200
    assert payload["ok"] is True
    assert payload["service"] == "nexus-openclaw-codex-harness-adapter"


def test_openclaw_codex_adapter_readyz_fails_closed_when_disabled(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path, enabled=False)
    payload = adapter.readiness_payload()

    assert payload["ok"] is False
    assert payload["reason"] == "openclaw_codex_runtime_disabled"
    assert payload["capabilities"]["official_openclaw_cli"] is True
    assert payload["capabilities"]["fixture_response"] is False
    assert payload["capabilities"]["hardcoded_nonce_echo"] is False


def test_openclaw_codex_adapter_readyz_200_with_official_cli_plugin_auth_and_model(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(adapter, "cli_path", lambda: "/usr/local/bin/openclaw")
    monkeypatch.setattr(adapter, "gateway_reachable", lambda: (_ for _ in ()).throw(AssertionError("gateway not required for local transport")))

    def fake_run(args, timeout_seconds, input_text=None):
        calls.append(args)
        if args[:2] == ["plugins", "list"]:
            return _completed(args, json.dumps({"plugins": [{"id": "codex", "package": "@openclaw/codex", "enabled": True}]}))
        if args[:3] == ["models", "auth", "list"]:
            return _completed(args, json.dumps({"profiles": [{"provider": "openai-codex", "status": "ok"}]}))
        if args[:3] == ["infer", "model", "run"]:
            prompt = args[args.index("--prompt") + 1]
            nonce = re.search(r"ready-\d+", prompt)
            assert nonce
            return _completed(args, _infer_envelope(_strict_reply(f"Readiness nonce {nonce.group(0)}")))
        raise AssertionError(args)

    monkeypatch.setattr(adapter, "run_openclaw", fake_run)
    payload = adapter.readiness_payload()

    assert payload["ok"] is True
    assert payload["adapter_stage"] == "p0_cli"
    assert payload["auth_provider"] == "openai-codex"
    assert payload["codex_plugin_package"] == "@openclaw/codex"
    assert payload["model"] == "openai/gpt-5.5"
    assert payload["infer_transport"] == "local"
    assert payload["gateway_required"] is False
    assert payload["local_infer_smoke_ready"] is True
    assert ["plugins", "list", "--json"] in calls
    assert ["models", "auth", "list", "--provider", "openai-codex", "--json"] in calls
    assert any(args[:4] == ["infer", "model", "run", "--local"] for args in calls)


def test_openclaw_codex_adapter_readyz_gateway_fails_closed_when_gateway_unreachable(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path, transport="gateway")
    monkeypatch.setattr(adapter, "cli_path", lambda: "/usr/local/bin/openclaw")
    monkeypatch.setattr(adapter, "gateway_reachable", lambda: False)

    def fake_run(args, timeout_seconds, input_text=None):
        if args[:2] == ["plugins", "list"]:
            return _completed(args, json.dumps({"plugins": [{"id": "codex", "package": "@openclaw/codex", "enabled": True}]}))
        if args[:3] == ["models", "auth", "list"]:
            return _completed(args, json.dumps({"profiles": [{"provider": "openai-codex", "status": "ok"}]}))
        raise AssertionError(args)

    monkeypatch.setattr(adapter, "run_openclaw", fake_run)
    payload = adapter.readiness_payload()

    assert payload["ok"] is False
    assert payload["infer_transport"] == "gateway"
    assert payload["gateway_required"] is True
    assert payload["gateway_ready"] is False
    assert payload["local_infer_smoke_ready"] is False
    assert payload["reason"] == "openclaw_codex_gateway_not_ready"


def test_openclaw_codex_adapter_readyz_gateway_ok_when_gateway_reachable(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path, transport="gateway")
    monkeypatch.setattr(adapter, "cli_path", lambda: "/usr/local/bin/openclaw")
    monkeypatch.setattr(adapter, "gateway_reachable", lambda: True)

    def fake_run(args, timeout_seconds, input_text=None):
        if args[:2] == ["plugins", "list"]:
            return _completed(args, json.dumps({"plugins": [{"id": "codex", "package": "@openclaw/codex", "enabled": True}]}))
        if args[:3] == ["models", "auth", "list"]:
            return _completed(args, json.dumps({"profiles": [{"provider": "openai-codex", "status": "ok"}]}))
        raise AssertionError(args)

    monkeypatch.setattr(adapter, "run_openclaw", fake_run)
    payload = adapter.readiness_payload()

    assert payload["ok"] is True
    assert payload["infer_transport"] == "gateway"
    assert payload["gateway_ready"] is True
    assert payload["local_infer_smoke_ready"] is False


def test_openclaw_codex_adapter_readyz_local_smoke_nonce_missing_fails(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path)
    monkeypatch.setattr(adapter, "cli_path", lambda: "/usr/local/bin/openclaw")

    def fake_run(args, timeout_seconds, input_text=None):
        if args[:2] == ["plugins", "list"]:
            return _completed(args, json.dumps({"plugins": [{"id": "codex", "package": "@openclaw/codex", "enabled": True}]}))
        if args[:3] == ["models", "auth", "list"]:
            return _completed(args, json.dumps({"profiles": [{"provider": "openai-codex", "status": "ok"}]}))
        if args[:3] == ["infer", "model", "run"]:
            return _completed(args, _infer_envelope(_strict_reply("Readiness model response without nonce")))
        raise AssertionError(args)

    monkeypatch.setattr(adapter, "run_openclaw", fake_run)
    payload = adapter.readiness_payload()

    assert payload["ok"] is False
    assert payload["local_infer_smoke_ready"] is False
    assert payload["reason"] == "openclaw_codex_local_infer_nonce_missing"


def test_openclaw_codex_auth_profiles_empty_is_not_ready(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path)
    monkeypatch.setattr(adapter, "cli_path", lambda: "/usr/local/bin/openclaw")

    def fake_run(args, timeout_seconds, input_text=None):
        if args[:2] == ["plugins", "list"]:
            return _completed(args, json.dumps({"plugins": [{"id": "codex", "package": "@openclaw/codex", "enabled": True}]}))
        if args[:3] == ["models", "auth", "list"]:
            return _completed(args, json.dumps({"profiles": []}))
        raise AssertionError(args)

    monkeypatch.setattr(adapter, "run_openclaw", fake_run)
    assert adapter.auth_ready() is False
    payload = adapter.readiness_payload()
    assert payload["ok"] is False
    assert payload["codex_plugin_ready"] is True
    assert payload["auth_ready"] is False
    assert payload["reason"] == "openclaw_codex_auth_not_ready"


def test_openclaw_codex_auth_active_profile_is_ready(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path)
    monkeypatch.setattr(adapter, "cli_path", lambda: "/usr/local/bin/openclaw")
    monkeypatch.setattr(
        adapter,
        "run_openclaw",
        lambda args, timeout_seconds, input_text=None: _completed(
            args,
            json.dumps({"profiles": [{"provider": "openai-codex", "status": "active"}]}),
        ),
    )
    assert adapter.auth_ready() is True


def test_openclaw_codex_auth_oauth_profile_with_future_expires_at_is_ready(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path)
    monkeypatch.setattr(adapter, "cli_path", lambda: "/usr/local/bin/openclaw")
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    monkeypatch.setattr(
        adapter,
        "run_openclaw",
        lambda args, timeout_seconds, input_text=None: _completed(
            args,
            json.dumps({"profiles": [{"provider": "openai-codex", "type": "oauth", "expiresAt": expires_at}]}),
        ),
    )
    assert adapter.auth_ready() is True


def test_openclaw_codex_auth_real_oauth_profile_shape_with_future_expires_at_is_ready(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path)
    monkeypatch.setattr(adapter, "cli_path", lambda: "/usr/local/bin/openclaw")

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 5, 24, 12, 0, 0, tzinfo=tz or timezone.utc)

    monkeypatch.setattr(adapter, "datetime", FixedDateTime)
    monkeypatch.setattr(
        adapter,
        "run_openclaw",
        lambda args, timeout_seconds, input_text=None: _completed(
            args,
            json.dumps(
                {
                    "profiles": [
                        {
                            "provider": "openai-codex",
                            "type": "oauth",
                            "expiresAt": "2026-06-03T19:45:44.586Z",
                        }
                    ]
                }
            ),
        ),
    )
    assert adapter.auth_ready() is True


def test_openclaw_codex_auth_oauth_profile_with_expired_expires_at_is_not_ready(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path)
    monkeypatch.setattr(adapter, "cli_path", lambda: "/usr/local/bin/openclaw")
    expires_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    monkeypatch.setattr(
        adapter,
        "run_openclaw",
        lambda args, timeout_seconds, input_text=None: _completed(
            args,
            json.dumps({"profiles": [{"provider": "openai-codex", "type": "oauth", "expiresAt": expires_at}]}),
        ),
    )
    assert adapter.auth_ready() is False


def test_openclaw_codex_auth_real_oauth_profile_shape_with_expired_expires_at_is_not_ready(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path)
    monkeypatch.setattr(adapter, "cli_path", lambda: "/usr/local/bin/openclaw")

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 6, 4, 12, 0, 0, tzinfo=tz or timezone.utc)

    monkeypatch.setattr(adapter, "datetime", FixedDateTime)
    monkeypatch.setattr(
        adapter,
        "run_openclaw",
        lambda args, timeout_seconds, input_text=None: _completed(
            args,
            json.dumps(
                {
                    "profiles": [
                        {
                            "provider": "openai-codex",
                            "type": "oauth",
                            "expiresAt": "2026-06-03T19:45:44.586Z",
                        }
                    ]
                }
            ),
        ),
    )
    assert adapter.auth_ready() is False


def test_openclaw_codex_auth_oauth_profile_without_expires_at_or_status_is_not_ready(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path)
    monkeypatch.setattr(adapter, "cli_path", lambda: "/usr/local/bin/openclaw")
    monkeypatch.setattr(
        adapter,
        "run_openclaw",
        lambda args, timeout_seconds, input_text=None: _completed(
            args,
            json.dumps({"profiles": [{"provider": "openai-codex", "type": "oauth"}]}),
        ),
    )
    assert adapter.auth_ready() is False


def test_openclaw_codex_auth_wrong_provider_with_future_expires_at_is_not_ready(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path)
    monkeypatch.setattr(adapter, "cli_path", lambda: "/usr/local/bin/openclaw")
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    monkeypatch.setattr(
        adapter,
        "run_openclaw",
        lambda args, timeout_seconds, input_text=None: _completed(
            args,
            json.dumps({"profiles": [{"provider": "openai", "type": "oauth", "expiresAt": expires_at}]}),
        ),
    )
    assert adapter.auth_ready() is False


def test_openclaw_codex_auth_wrong_provider_is_not_ready(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path)
    monkeypatch.setattr(adapter, "cli_path", lambda: "/usr/local/bin/openclaw")
    monkeypatch.setattr(
        adapter,
        "run_openclaw",
        lambda args, timeout_seconds, input_text=None: _completed(
            args,
            json.dumps({"profiles": [{"provider": "openai", "status": "active"}]}),
        ),
    )
    assert adapter.auth_ready() is False


def test_openclaw_codex_plugin_installed_but_not_enabled_is_not_ready(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path)
    monkeypatch.setattr(adapter, "cli_path", lambda: "/usr/local/bin/openclaw")
    monkeypatch.setattr(
        adapter,
        "run_openclaw",
        lambda args, timeout_seconds, input_text=None: _completed(
            args,
            json.dumps({"plugins": [{"id": "codex", "package": "@openclaw/codex", "enabled": False}]}),
        ),
    )
    assert adapter.plugin_ready() is False


def test_openclaw_codex_plugin_enabled_by_id_is_ready(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path)
    monkeypatch.setattr(adapter, "cli_path", lambda: "/usr/local/bin/openclaw")
    monkeypatch.setattr(
        adapter,
        "run_openclaw",
        lambda args, timeout_seconds, input_text=None: _completed(
            args,
            json.dumps({"plugins": [{"id": "codex", "package": "@openclaw/codex", "enabled": True}]}),
        ),
    )
    assert adapter.plugin_ready() is True


def test_openclaw_codex_plugin_enabled_by_package_status_is_ready(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path)
    monkeypatch.setattr(adapter, "cli_path", lambda: "/usr/local/bin/openclaw")
    monkeypatch.setattr(
        adapter,
        "run_openclaw",
        lambda args, timeout_seconds, input_text=None: _completed(
            args,
            json.dumps({"plugins": [{"package": "@openclaw/codex", "status": "enabled"}]}),
        ),
    )
    assert adapter.plugin_ready() is True


def test_openclaw_codex_provider_capability_on_openai_plugin_is_ready(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path)
    monkeypatch.setattr(adapter, "cli_path", lambda: "/usr/local/bin/openclaw")
    monkeypatch.setattr(
        adapter,
        "run_openclaw",
        lambda args, timeout_seconds, input_text=None: _completed(
            args,
            json.dumps({"plugins": [{"id": "openai", "enabled": True, "status": "loaded", "providerIds": ["openai", "openai-codex"]}]}),
        ),
    )
    assert adapter.plugin_ready() is True


def test_openclaw_openai_plugin_without_codex_provider_is_not_ready(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path)
    monkeypatch.setattr(adapter, "cli_path", lambda: "/usr/local/bin/openclaw")
    monkeypatch.setattr(
        adapter,
        "run_openclaw",
        lambda args, timeout_seconds, input_text=None: _completed(
            args,
            json.dumps({"plugins": [{"id": "openai", "enabled": True, "status": "loaded", "providerIds": ["openai"]}]}),
        ),
    )
    assert adapter.plugin_ready() is False


def test_openclaw_disabled_openai_plugin_with_codex_provider_is_not_ready(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path)
    monkeypatch.setattr(adapter, "cli_path", lambda: "/usr/local/bin/openclaw")
    monkeypatch.setattr(
        adapter,
        "run_openclaw",
        lambda args, timeout_seconds, input_text=None: _completed(
            args,
            json.dumps({"plugins": [{"id": "openai", "enabled": False, "status": "loaded", "providerIds": ["openai", "openai-codex"]}]}),
        ),
    )
    assert adapter.plugin_ready() is False


def test_openclaw_codex_adapter_reply_invokes_official_infer_and_returns_strict_json(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path)
    captured: dict = {}
    monkeypatch.setattr(adapter, "readiness_payload", lambda: {"ok": True})
    monkeypatch.setattr(adapter, "cli_path", lambda: "/usr/local/bin/openclaw")

    def fake_run(args, timeout_seconds, input_text=None):
        captured["args"] = args
        captured["timeout"] = timeout_seconds
        envelope = {"ok": True, "outputs": [{"text": json.dumps(_strict_reply("Echo nonce-openclaw"))}]}
        return _completed(args, json.dumps(envelope))

    monkeypatch.setattr(adapter, "run_openclaw", fake_run)
    server = adapter.ThreadingHTTPServer(("127.0.0.1", 0), adapter.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _post_json(
            f"http://127.0.0.1:{server.server_address[1]}/reply",
            _reply_payload("Echo this nonce exactly: nonce-openclaw"),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 200
    assert payload == _strict_reply("Echo nonce-openclaw")
    assert captured["args"][:4] == ["infer", "model", "run", "--local"]
    assert "--model" in captured["args"]
    assert "openai/gpt-5.5" in captured["args"]
    assert "--json" in captured["args"]
    prompt = captured["args"][captured["args"].index("--prompt") + 1]
    assert "Return only strict JSON" in prompt
    assert "Do not perform browser cookie scraping" in prompt
    assert "nonce-openclaw" in prompt
    assert "oauth-access" not in json.dumps(payload)


def test_openclaw_codex_adapter_invalid_model_output_is_rejected(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path)
    monkeypatch.setattr(adapter, "readiness_payload", lambda: {"ok": True})
    monkeypatch.setattr(adapter, "cli_path", lambda: "/usr/local/bin/openclaw")
    monkeypatch.setattr(adapter, "run_openclaw", lambda args, timeout_seconds, input_text=None: _completed(args, json.dumps({"output_text": "not-json"})))
    server = adapter.ThreadingHTTPServer(("127.0.0.1", 0), adapter.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _post_json(f"http://127.0.0.1:{server.server_address[1]}/reply", _reply_payload())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 502
    assert payload["error"] == "openclaw_codex_invalid_json_output"
    assert "oauth-access" not in json.dumps(payload)


def test_openclaw_codex_adapter_timeout_returns_fail_closed(monkeypatch, tmp_path):
    adapter = _load_adapter(monkeypatch, tmp_path)
    monkeypatch.setattr(adapter, "readiness_payload", lambda: {"ok": True})
    monkeypatch.setattr(adapter, "cli_path", lambda: "/usr/local/bin/openclaw")
    monkeypatch.setattr(
        adapter,
        "run_openclaw",
        lambda args, timeout_seconds, input_text=None: (_ for _ in ()).throw(subprocess.TimeoutExpired(args, timeout_seconds)),
    )
    server = adapter.ThreadingHTTPServer(("127.0.0.1", 0), adapter.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _post_json(f"http://127.0.0.1:{server.server_address[1]}/reply", _reply_payload())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 504
    assert payload == {"ok": False, "error": "openclaw_codex_timeout"}


def test_openclaw_codex_adapter_source_keeps_execution_boundary_safe():
    source = (ROOT / "deploy" / "codex_openclaw_codex_harness_adapter.py").read_text(encoding="utf-8")

    assert "shell=False" in source
    assert "shell=True" not in source
    assert "OPENAI_API_KEY" in source
    assert "provider_routing_rules" not in source
    assert "canary_percent" not in source
    assert "ticket" not in source.lower() or "direct_ticket_action" in source
    assert "hardcoded_nonce_echo" in source
