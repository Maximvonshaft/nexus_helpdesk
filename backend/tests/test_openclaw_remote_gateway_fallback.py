from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/helpdesk_suite_openclaw_remote_gateway_test.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_remote_gateway_mcp_client_guard_prevents_subprocess(monkeypatch):
    from app.services import openclaw_mcp_client as mcp

    monkeypatch.setattr(mcp.settings, "openclaw_deployment_mode", "remote_gateway")
    monkeypatch.setattr(mcp.settings, "openclaw_bridge_enabled", True)
    monkeypatch.setattr(mcp.settings, "openclaw_cli_fallback_enabled", False)

    events: list[str] = []

    def fake_log_event(level, name, **payload):
        events.append(name)

    def fake_popen(*args, **kwargs):
        raise AssertionError("local openclaw subprocess must not be spawned")

    monkeypatch.setattr(mcp, "log_event", fake_log_event)
    monkeypatch.setattr(mcp.subprocess, "Popen", fake_popen)

    with pytest.raises(mcp.OpenClawMCPError, match="local_openclaw_mcp_cli_disabled"):
        mcp.OpenClawMCPClient().start()

    assert "openclaw_mcp_start" not in events


def test_remote_gateway_runtime_probe_uses_http_bridge_not_mcp(monkeypatch):
    from app.services import openclaw_runtime_service as runtime

    monkeypatch.setattr(runtime.settings, "openclaw_deployment_mode", "remote_gateway")
    monkeypatch.setattr(runtime.settings, "openclaw_bridge_enabled", True)
    monkeypatch.setattr(runtime.settings, "openclaw_bridge_url", "http://bridge.example")
    monkeypatch.setattr(runtime.settings, "openclaw_cli_fallback_enabled", False)
    monkeypatch.setattr(runtime.settings, "openclaw_transport", "mcp")

    class BombMCP:
        def __init__(self, *args, **kwargs):
            raise AssertionError("remote_gateway probe must not instantiate OpenClawMCPClient")

    class FakeBridge:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def conversations_list(self, *, limit=1, agent="support"):
            return {"conversations": [{"sessionKey": "sess-ok"}]}

    monkeypatch.setattr(runtime, "OpenClawMCPClient", BombMCP)
    monkeypatch.setattr(runtime, "OpenClawBridgeHTTPClient", lambda *args, **kwargs: FakeBridge())

    result = runtime.probe_openclaw_connectivity()

    assert result.conversations_tool_ok is True
    assert result.conversations_seen == 1
    assert result.sample_session_key == "sess-ok"


def test_remote_gateway_runtime_probe_degrades_when_bridge_unreachable(monkeypatch):
    from app.services import openclaw_runtime_service as runtime

    monkeypatch.setattr(runtime.settings, "openclaw_deployment_mode", "remote_gateway")
    monkeypatch.setattr(runtime.settings, "openclaw_bridge_enabled", True)
    monkeypatch.setattr(runtime.settings, "openclaw_bridge_url", "http://bridge.example")
    monkeypatch.setattr(runtime.settings, "openclaw_cli_fallback_enabled", False)
    monkeypatch.setattr(runtime.settings, "openclaw_transport", "mcp")

    class BombMCP:
        def __init__(self, *args, **kwargs):
            raise AssertionError("remote_gateway degraded probe must not instantiate OpenClawMCPClient")

    class FailingBridge:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def conversations_list(self, *, limit=1, agent="support"):
            raise runtime.OpenClawBridgeHTTPError("timed out")

    monkeypatch.setattr(runtime, "OpenClawMCPClient", BombMCP)
    monkeypatch.setattr(runtime, "OpenClawBridgeHTTPClient", lambda *args, **kwargs: FailingBridge())

    result = runtime.probe_openclaw_connectivity()

    assert result.conversations_tool_ok is False
    assert any("openclaw_bridge_unreachable" in item for item in result.warnings)


def test_remote_gateway_bridge_list_degrades_without_local_mcp(monkeypatch):
    from app.services import openclaw_bridge

    monkeypatch.setattr(openclaw_bridge.settings, "openclaw_deployment_mode", "remote_gateway")
    monkeypatch.setattr(openclaw_bridge.settings, "openclaw_bridge_enabled", True)
    monkeypatch.setattr(openclaw_bridge.settings, "openclaw_cli_fallback_enabled", False)
    monkeypatch.setattr(openclaw_bridge, "list_openclaw_bridge_conversations", lambda **kwargs: None)

    class BombMCP:
        def __init__(self, *args, **kwargs):
            raise AssertionError("remote_gateway conversation list must not instantiate local MCP client")

    monkeypatch.setattr(openclaw_bridge, "OpenClawMCPClient", BombMCP)

    payload = openclaw_bridge.list_openclaw_conversations(limit=1)

    assert payload["conversations"] == []
    assert payload["degraded"] is True
    assert payload["degraded_reason"] == "openclaw_bridge_unreachable"
