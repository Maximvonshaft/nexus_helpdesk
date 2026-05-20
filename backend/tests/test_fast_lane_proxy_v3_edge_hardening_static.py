from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROXY_PATH = ROOT / "deploy" / "openclaw_bridge_responses_proxy.py"
NGINX_HARDENING_PATH = ROOT / "deploy" / "nginx" / "nexus_deny_sensitive_paths.conf"


def load_proxy_module():
    spec = importlib.util.spec_from_file_location("openclaw_bridge_responses_proxy", PROXY_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_openclaw_responses_proxy_v3_normalizes_plain_text_to_fast_reply_contract():
    proxy = load_proxy_module()

    result = proxy.normalize_fast_reply("Please provide your tracking number.", "Where is my parcel?")

    assert proxy.VERSION == "3.0"
    assert result == {
        "reply": "Please provide your tracking number.",
        "intent": "other",
        "tracking_number": None,
        "handoff_required": False,
        "handoff_reason": None,
        "recommended_agent_action": None,
    }


def test_openclaw_responses_proxy_v3_preserves_valid_strict_json_reply():
    proxy = load_proxy_module()
    raw = json.dumps({
        "reply": "Thanks, we are checking this now.",
        "intent": "tracking",
        "tracking_number": "SPX123456789",
        "handoff_required": False,
        "handoff_reason": None,
        "recommended_agent_action": None,
    })

    result = proxy.normalize_fast_reply(raw, "Track SPX123456789")

    assert result["reply"] == "Thanks, we are checking this now."
    assert result["intent"] == "tracking"
    assert result["tracking_number"] == "SPX123456789"
    assert result["handoff_required"] is False


def test_openclaw_responses_proxy_v3_emits_openai_responses_compatible_payload():
    proxy = load_proxy_module()
    strict_reply = {
        "reply": "Hello, how can I help?",
        "intent": "other",
        "tracking_number": None,
        "handoff_required": False,
        "handoff_reason": None,
        "recommended_agent_action": None,
    }

    payload = proxy.make_responses_payload(strict_reply, {"ok": True, "status": "completed"}, 123, "openclaw:support")

    assert payload["object"] == "response"
    assert payload["status"] == "completed"
    assert payload["model"] == "openclaw:support"
    assert payload["metadata"]["version"] == "3.0"
    assert payload["metadata"]["normalized_fast_reply"] is True
    assert json.loads(payload["output_text"]) == strict_reply
    assert payload["output"][0]["content"][0]["type"] == "output_text"


def test_sensitive_path_hardening_blocks_common_secret_probe_paths():
    text = NGINX_HARDENING_PATH.read_text(encoding="utf-8")

    assert "return 404" in text
    for pattern in [
        ".env",
        ".git",
        "wp-config\\.php",
        "phpinfo\\.php",
        "credentials\\.json",
        "private\\.key",
        "storage/logs/laravel\\.log",
    ]:
        assert pattern in text
