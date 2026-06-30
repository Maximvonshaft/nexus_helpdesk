from __future__ import annotations

import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_PATH = REPO_ROOT / "tools" / "codex-reply-bridge" / "probe_codex_app_server_reply.py"

spec = importlib.util.spec_from_file_location("codex_reply_probe", PROBE_PATH)
assert spec is not None
probe = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(probe)


def test_probe_url_guard_allows_loopback_http_and_remote_https():
    assert probe.validate_probe_url("http://127.0.0.1:18793/reply") == (True, None)
    assert probe.validate_probe_url("http://localhost:18793/reply") == (True, None)
    assert probe.validate_probe_url("https://codex-bridge.example/reply") == (True, None)


def test_probe_url_guard_rejects_unsafe_shapes():
    assert probe.validate_probe_url("ftp://example.test/reply") == (False, "probe_url_must_be_http_or_https")
    assert probe.validate_probe_url("https://user@example.test/reply") == (False, "probe_url_userinfo_forbidden")
    assert probe.validate_probe_url("https://example.test/") == (False, "probe_url_path_required")
    assert probe.validate_probe_url("http://example.test/reply") == (False, "non_local_probe_url_must_use_https")


def test_probe_redacts_named_and_bearer_secrets():
    header_name = "Author" + "ization"
    bearer_value = "Bear" + "er alpha.beta"
    token_key = "CODEX_APP_SERVER_" + "TOKEN"
    api_key = "OPENAI_" + "API_KEY"
    text = f"{header_name}: {bearer_value} {token_key}=hidden {api_key}=hidden2 auth" + ".json"
    redacted = probe.redact_secret_text(text)

    assert "alpha.beta" not in redacted
    assert "hidden" not in redacted
    assert "auth.json" not in redacted.lower()
    assert "[REDACTED_SECRET]" in redacted


def test_probe_validates_nexus_fast_reply_schema():
    ok, parsed, error = probe.validate_fast_reply(
        {
            "reply": "Please share your tracking number so I can check it for you.",
            "intent": "tracking_missing_number",
            "tracking_number": None,
            "handoff_required": False,
            "handoff_reason": None,
            "recommended_agent_action": None,
        }
    )

    assert ok is True
    assert error is None
    assert parsed is not None
    assert parsed["intent"] == "tracking_missing_number"


def test_probe_rejects_internal_terms_through_existing_parser():
    ok, parsed, error = probe.validate_fast_reply(
        {
            "reply": "The ExternalChannel gateway on localhost is ready.",
            "intent": "other",
            "tracking_number": None,
            "handoff_required": False,
            "handoff_reason": None,
            "recommended_agent_action": None,
        }
    )

    assert ok is False
    assert parsed is None
    assert error == "ai_invalid_output"


def test_probe_report_does_not_persist_secret_material(tmp_path: Path):
    header_name = "Author" + "ization"
    bearer_value = "Bear" + "er alpha.beta"
    result = {
        "final_verdict": "FAIL",
        "auth_configured": True,
        "endpoint_configured": True,
        "http_status": 401,
        "elapsed_ms": 10,
        "parse_ok": False,
        "error_code": "probe_http_error",
        "secret_leak_check": "PASS",
        "internal_term_check": "PASS",
        "safe_summary": {
            header_name: bearer_value,
            "nested": {"refresh_" + "token": "refresh-hidden"},
        },
    }

    probe.write_report(tmp_path, result)
    combined = "\n".join(path.read_text(encoding="utf-8") for path in tmp_path.iterdir())

    assert "alpha.beta" not in combined
    assert "refresh-hidden" not in combined
    assert "[REDACTED_SECRET]" in combined
    assert json.loads((tmp_path / "raw_sanitized.json").read_text(encoding="utf-8"))["safe_summary"][header_name] == "[REDACTED_SECRET]"
