from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_security_remediation_removes_sensitive_exception_and_log_flows() -> None:
    provider = (ROOT / "backend/app/services/provider_runtime_status.py").read_text(encoding="utf-8")
    support = (ROOT / "backend/app/services/support_intelligence_service.py").read_text(encoding="utf-8")
    audit = (ROOT / "backend/app/services/webchat_ai_decision_runtime/audit.py").read_text(encoding="utf-8")
    readiness = (ROOT / "backend/scripts/validate_production_readiness.py").read_text(encoding="utf-8")
    logs = (ROOT / "backend/app/services/webchat_service.py").read_text(encoding="utf-8")

    assert "warnings.append(str(exc))" not in provider
    assert '"config_error": str(exc)' not in provider
    assert "bridge_client" not in support
    assert '"payload": safe_payload' not in audit
    assert '"payload_summary": payload_summary' in audit
    assert "livekit_api_key_configured" not in readiness
    assert "livekit_api_secret_configured" not in readiness
    logger_calls = [
        ast.unparse(node)
        for node in ast.walk(ast.parse(logs))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "WEBCHAT_LOGGER"
    ]
    assert logger_calls
    assert all("payload." not in call for call in logger_calls)


def test_voice_paths_and_html_are_bounded() -> None:
    main = (ROOT / "backend/app/main.py").read_text(encoding="utf-8")
    live_voice = (ROOT / "infra/private-ai-runtime/live_voice_runtime/app.py").read_text(encoding="utf-8")
    assert "html.escape(safe_session_id, quote=True)" in main
    assert "tempfile.mkstemp" in live_voice
    assert "self.voice_session_id}_{turn_id}" not in live_voice


def test_widget_avatar_is_same_origin_and_not_configurable() -> None:
    widget = (ROOT / "backend/app/static/webchat/widget.js").read_text(encoding="utf-8")
    voice_entry = (ROOT / "backend/app/static/webchat/voice-entry.js").read_text(encoding="utf-8")
    demo = (ROOT / "backend/app/static/webchat/demo/index.html").read_text(encoding="utf-8")
    docs = (ROOT / "docs/webchat-widget.md").read_text(encoding="utf-8")
    assert "data-avatar-url" not in widget
    assert "data-avatar-url" not in voice_entry
    assert "data-avatar-url" not in demo
    assert "data-avatar-url" not in docs
    assert "new URL('/webchat/demo/assets/speedaf-ai-bot-avatar.png', scriptUrl.origin)" in widget
    assert "/^#[0-9a-f]{6}$/i.test(requestedAccentColor)" in widget


def test_integration_secret_delivery_is_stdin_only_and_never_echoed() -> None:
    script_path = ROOT / "backend/scripts/create_integration_client.py"
    source = script_path.read_text(encoding="utf-8")
    assert 'parser.add_argument("--secret"' not in source
    assert "SECRET={secret}" not in source
    assert "--secret-stdin" in source
    assert "sys.stdin.readline().strip()" in source
    assert '"secret_delivery": "stdin"' in source


def test_codeql_exception_policy_has_no_stale_exceptions() -> None:
    payload = json.loads((ROOT / "config/security/codeql-exceptions.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == "nexus_codeql_exception_policy_v1"
    assert payload["exceptions"] == []

    protocol = (ROOT / "backend/app/services/speedaf/track_query.py").read_text(encoding="utf-8")
    assert "# codeql[py/weak-sensitive-data-hashing]" in protocol
    assert "hashlib.md5(payload, usedforsecurity=False)" in protocol
