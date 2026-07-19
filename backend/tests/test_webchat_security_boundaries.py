from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_server_owns_webchat_origin_policy() -> None:
    origin = _read("backend/app/services/webchat_origin_policy.py")
    public_api = _read("backend/app/api/webchat_public.py")
    websocket_api = _read("backend/app/api/webchat_ws.py")
    settings = _read("backend/app/settings.py")

    assert "evaluate_webchat_origin" in origin
    assert "webchat_origin_policy" in public_api
    assert "webchat_origin_policy" in websocket_api
    assert "WEBCHAT_ALLOW_NO_ORIGIN" in settings
    assert "WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT" in settings


def test_webchat_token_transport_is_header_or_same_origin_only() -> None:
    public_api = _read("backend/app/api/webchat_public.py")
    widget = _read("backend/app/static/webchat/widget.js")
    assert "X-Webchat-Visitor-Token" in public_api
    assert "X-Webchat-Visitor-Token" in widget
    assert "visitor_token: state.visitorToken" not in widget


def test_public_webchat_payload_does_not_expose_internal_identifiers() -> None:
    projection = _read("backend/app/services/webchat_public_payload.py")
    for forbidden in (
        "provider_group_id",
        "internal_note",
        "actor_user_id",
        "assignee_id",
        "team_id",
    ):
        assert forbidden not in projection


def test_public_webchat_rejects_unbounded_message_content() -> None:
    schemas = _read("backend/app/webchat_schemas.py")
    assert "max_length" in schemas
    assert "WebchatMessageCreate" in schemas


def test_websocket_has_bounded_connection_and_heartbeat_contracts() -> None:
    settings = _read("backend/app/settings.py")
    websocket_api = _read("backend/app/api/webchat_ws.py")
    for marker in (
        "WEBCHAT_WS_MAX_CONNECTIONS",
        "WEBCHAT_WS_MAX_CONNECTIONS_PER_USER",
        "WEBCHAT_WS_HEARTBEAT_MS",
        "WEBCHAT_WS_HELLO_TIMEOUT_MS",
    ):
        assert marker in settings
    assert "invalid_json" in websocket_api
    assert "heartbeat" in websocket_api.lower()


def test_webchat_public_surface_is_not_an_operator_console() -> None:
    lifecycle = _read("config/architecture/compatibility-lifecycle.v1.json")
    readme = _read("README.md")
    assert '"kind": "separate-public-product-surface"' in lifecycle
    assert "not-an-operator-console" in lifecycle
    assert "separate public surface" in readme


def test_retired_parallel_webchat_authorities_are_absent() -> None:
    for path in (
        "frontend",
        "webapp/src/features/support-console",
        "webapp/src/lib/webchatRealtime.ts",
        "backend/app/services/external_channel_bridge.py",
        "backend/app/services/external_channel_runtime_service.py",
    ):
        assert not (ROOT / path).exists(), path
