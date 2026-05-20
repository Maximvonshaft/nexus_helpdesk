#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.webchat_fast_output_parser import FastReplyParseError, parse_openclaw_fast_reply

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from upstream_auth_discovery import (  # noqa: E402
    auth_source_public_summary,
    discover_auth_sources,
    select_best_auth_source,
)
from upstream_login_payload_boundary import (  # noqa: E402
    build_best_login_payload,
    login_payload_safe_summary,
)
from upstream_reply_transport import (  # noqa: E402
    ReplyTransportSettings,
    normalize_reply_path,
    post_reply_turn,
)
from upstream_transport_boundary import (  # noqa: E402
    TransportBoundarySettings,
    post_account_login_start,
    validate_private_app_server_url,
)


AdapterMode = Literal["disabled", "contract_fixture", "codex_app_server"]


class ReplyRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=160)
    tenant_key: str = Field(default="default", max_length=120)
    channel_key: str = Field(default="website", max_length=120)
    session_id: str = Field(min_length=1, max_length=240)
    body: str = Field(min_length=1, max_length=8000)
    recent_context: list[dict[str, Any]] = Field(default_factory=list)
    tracking_fact_summary: str | None = Field(default=None, max_length=4000)
    tracking_fact_evidence_present: bool = False
    strict_schema: str = Field(default="speedaf_webchat_fast_reply_v1", max_length=120)


@dataclass(frozen=True)
class UpstreamAdapterSettings:
    mode: AdapterMode
    app_env: str
    require_auth: bool
    shared_token: str | None
    auth_profile_file: str | None
    codex_cli_auth_file: str | None
    api_key_file: str | None
    app_server_base_url: str | None
    app_server_timeout_ms: int
    app_server_allow_public_url: bool
    app_server_login_dry_run: bool
    app_server_reply_enabled: bool
    app_server_reply_path: str
    app_server_reply_token: str | None


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _read_secret_file(path_value: str | None) -> str | None:
    if not path_value:
        return None
    try:
        value = Path(path_value).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if value.lower().startswith("bearer "):
        value = value.split(None, 1)[1].strip()
    return value or None


def _load_settings() -> UpstreamAdapterSettings:
    raw_mode = (os.getenv("CODEX_UPSTREAM_ADAPTER_MODE") or "disabled").strip().lower()
    mode: AdapterMode = raw_mode if raw_mode in {"disabled", "contract_fixture", "codex_app_server"} else "disabled"  # type: ignore[assignment]
    shared_token = _read_secret_file(os.getenv("CODEX_UPSTREAM_ADAPTER_SHARED_TOKEN_FILE"))
    shared_token = shared_token or (os.getenv("CODEX_UPSTREAM_ADAPTER_SHARED_TOKEN") or "").strip() or None
    reply_token = _read_secret_file(os.getenv("CODEX_UPSTREAM_APP_SERVER_REPLY_TOKEN_FILE"))
    reply_token = reply_token or (os.getenv("CODEX_UPSTREAM_APP_SERVER_REPLY_TOKEN") or "").strip() or None
    return UpstreamAdapterSettings(
        mode=mode,
        app_env=(os.getenv("APP_ENV") or "development").strip().lower(),
        require_auth=_env_bool("CODEX_UPSTREAM_ADAPTER_REQUIRE_AUTH", True),
        shared_token=shared_token,
        auth_profile_file=(os.getenv("CODEX_UPSTREAM_AUTH_PROFILE_FILE") or "").strip() or None,
        codex_cli_auth_file=(os.getenv("CODEX_CLI_AUTH_FILE") or "").strip() or None,
        api_key_file=(os.getenv("CODEX_UPSTREAM_API_KEY_FILE") or "").strip() or None,
        app_server_base_url=(os.getenv("CODEX_UPSTREAM_APP_SERVER_BASE_URL") or "").strip() or None,
        app_server_timeout_ms=_env_int("CODEX_UPSTREAM_APP_SERVER_TIMEOUT_MS", 15000, minimum=500, maximum=30000),
        app_server_allow_public_url=_env_bool("CODEX_UPSTREAM_APP_SERVER_ALLOW_PUBLIC_URL", False),
        app_server_login_dry_run=_env_bool("CODEX_UPSTREAM_APP_SERVER_LOGIN_DRY_RUN", True),
        app_server_reply_enabled=_env_bool("CODEX_UPSTREAM_APP_SERVER_REPLY_ENABLED", False),
        app_server_reply_path=(os.getenv("CODEX_UPSTREAM_APP_SERVER_REPLY_PATH") or "/reply").strip() or "/reply",
        app_server_reply_token=reply_token,
    )


def _safe_error(status_code: int, error_code: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"ok": False, "error_code": error_code})


def _auth_token_from_headers(authorization: str | None, x_nexus_upstream_token: str | None) -> str | None:
    if x_nexus_upstream_token and x_nexus_upstream_token.strip():
        return x_nexus_upstream_token.strip()
    if not authorization:
        return None
    value = authorization.strip()
    if value.lower().startswith("bearer "):
        return value.split(None, 1)[1].strip()
    return None


def _assert_authorized(
    settings: UpstreamAdapterSettings,
    authorization: str | None,
    x_nexus_upstream_token: str | None,
) -> None:
    if not settings.require_auth:
        return
    if not settings.shared_token:
        raise HTTPException(status_code=503, detail="upstream_adapter_auth_not_configured")
    supplied = _auth_token_from_headers(authorization, x_nexus_upstream_token)
    if not supplied or supplied != settings.shared_token:
        raise HTTPException(status_code=401, detail="upstream_adapter_auth_failed")


def _fixture_reply(request: ReplyRequest) -> dict[str, Any]:
    if request.tracking_fact_summary and request.tracking_fact_evidence_present:
        return {
            "reply": "I found the available parcel information. Please check the latest tracking details in your shipment page.",
            "intent": "tracking",
            "tracking_number": None,
            "handoff_required": False,
            "handoff_reason": None,
            "recommended_agent_action": None,
        }
    return {
        "reply": "Please share your tracking number so I can check your parcel status.",
        "intent": "tracking_missing_number",
        "tracking_number": None,
        "handoff_required": False,
        "handoff_reason": None,
        "recommended_agent_action": None,
    }


def _normalize_strict_reply(payload: Any) -> dict[str, Any]:
    parsed = parse_openclaw_fast_reply(payload)
    return {
        "reply": parsed.reply,
        "intent": parsed.intent,
        "tracking_number": parsed.tracking_number,
        "handoff_required": parsed.handoff_required,
        "handoff_reason": parsed.handoff_reason,
        "recommended_agent_action": parsed.recommended_agent_action,
    }


def _auth_discovery(settings: UpstreamAdapterSettings) -> dict[str, Any]:
    candidates = discover_auth_sources(
        auth_profile_file=settings.auth_profile_file,
        codex_cli_auth_file=settings.codex_cli_auth_file,
        api_key_file=settings.api_key_file,
    )
    selected = select_best_auth_source(candidates)
    return {
        "selected": auth_source_public_summary(selected),
        "candidates": [auth_source_public_summary(candidate) for candidate in candidates],
    }


def _login_payload_result(settings: UpstreamAdapterSettings):
    return build_best_login_payload(
        auth_profile_file=settings.auth_profile_file,
        codex_cli_auth_file=settings.codex_cli_auth_file,
        api_key_file=settings.api_key_file,
    )


def _login_payload_boundary(settings: UpstreamAdapterSettings) -> dict[str, Any]:
    return login_payload_safe_summary(_login_payload_result(settings))


def _transport_boundary_status(settings: UpstreamAdapterSettings) -> dict[str, Any]:
    normalized_url, error_code = validate_private_app_server_url(
        settings.app_server_base_url,
        allow_public_url=settings.app_server_allow_public_url,
    )
    reply_path, reply_path_error = normalize_reply_path(settings.app_server_reply_path)
    return {
        "configured": bool(settings.app_server_base_url),
        "base_url_accepted": bool(normalized_url and not error_code),
        "error_code": error_code,
        "timeout_ms": settings.app_server_timeout_ms,
        "allow_public_url": settings.app_server_allow_public_url,
        "login_dry_run": settings.app_server_login_dry_run,
        "account_login_start_request": False,
        "reply_enabled": settings.app_server_reply_enabled,
        "reply_path": reply_path,
        "reply_path_error_code": reply_path_error,
        "reply_bearer_token_configured": bool(settings.app_server_reply_token),
        "external_network_call": False,
    }


def _provider_capabilities(settings: UpstreamAdapterSettings) -> dict[str, Any]:
    transport = _transport_boundary_status(settings)
    auth = _auth_discovery(settings)
    return {
        "provider": "codex_app_server",
        "runtime": "private_upstream_adapter",
        "mode": settings.mode,
        "capabilities": {
            "webchat_fast_reply": bool(
                settings.mode == "codex_app_server"
                and settings.app_server_reply_enabled
                and auth["selected"]["usable"]
                and transport["base_url_accepted"]
                and transport["reply_path"]
                and not transport["reply_path_error_code"]
            ),
            "account_login_start": bool(settings.mode == "codex_app_server" and auth["selected"]["usable"] and transport["base_url_accepted"]),
            "streaming": False,
            "tool_execution": False,
            "ticket_action": False,
            "handoff_decision": True,
        },
        "safety_level": "reply_only",
        "boundary": {
            "browser_cookie_scraping": False,
            "chatgpt_session_scraping": False,
            "shell_execution": False,
            "file_write": False,
            "tool_execution": False,
            "direct_ticket_action": False,
            "direct_customer_outbound_send": False,
        },
    }


app = FastAPI(title="NexusDesk Codex Upstream Adapter", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "service": "codex-upstream-adapter"}


@app.get("/readyz", response_model=None)
def readyz():
    settings = _load_settings()
    if settings.mode == "disabled":
        return _safe_error(503, "upstream_adapter_disabled")
    if settings.require_auth and not settings.shared_token:
        return _safe_error(503, "upstream_adapter_auth_not_configured")
    if settings.mode == "codex_app_server" and not _auth_discovery(settings)["selected"]["usable"]:
        return _safe_error(503, "codex_auth_source_missing")
    return {"ok": True, "mode": settings.mode, "auth_required": settings.require_auth, "capabilities": _provider_capabilities(settings)["capabilities"]}


@app.get("/auth/status")
def auth_status(authorization: str | None = Header(default=None), x_nexus_upstream_token: str | None = Header(default=None)) -> dict[str, Any]:
    settings = _load_settings()
    supplied = _auth_token_from_headers(authorization, x_nexus_upstream_token)
    return {
        "ok": True,
        "mode": settings.mode,
        "auth_required": settings.require_auth,
        "shared_token_configured": bool(settings.shared_token),
        "request_token_present": bool(supplied),
        "discovery": _auth_discovery(settings),
        "login_payload_boundary": _login_payload_boundary(settings),
        "transport_boundary": _transport_boundary_status(settings),
        "provider_runtime": _provider_capabilities(settings),
    }


@app.get("/provider/status")
def provider_status(authorization: str | None = Header(default=None), x_nexus_upstream_token: str | None = Header(default=None)) -> dict[str, Any]:
    settings = _load_settings()
    _assert_authorized(settings, authorization, x_nexus_upstream_token)
    return {"ok": True, "provider_runtime": _provider_capabilities(settings), "transport_boundary": _transport_boundary_status(settings)}


@app.get("/transport/status")
def transport_status(authorization: str | None = Header(default=None), x_nexus_upstream_token: str | None = Header(default=None)) -> dict[str, Any]:
    settings = _load_settings()
    _assert_authorized(settings, authorization, x_nexus_upstream_token)
    return {
        "ok": True,
        "mode": settings.mode,
        "login_payload_boundary": _login_payload_boundary(settings),
        "transport_boundary": _transport_boundary_status(settings),
    }


@app.post("/transport/login-start", response_model=None)
def transport_login_start(authorization: str | None = Header(default=None), x_nexus_upstream_token: str | None = Header(default=None)):
    settings = _load_settings()
    _assert_authorized(settings, authorization, x_nexus_upstream_token)
    if settings.mode != "codex_app_server":
        return _safe_error(409, "codex_app_server_mode_required")
    login_result = _login_payload_result(settings)
    if not login_result.payload:
        return _safe_error(503, login_result.error_code or "login_payload_not_ready")
    if settings.app_server_login_dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "login_payload_boundary": login_payload_safe_summary(login_result),
            "transport_boundary": _transport_boundary_status(settings),
        }
    result = asyncio.run(
        post_account_login_start(
            settings=TransportBoundarySettings(
                app_server_base_url=settings.app_server_base_url,
                timeout_ms=settings.app_server_timeout_ms,
                allow_public_url=settings.app_server_allow_public_url,
            ),
            login_payload=login_result.payload,
        )
    )
    return JSONResponse(status_code=200 if result.ok else 502, content={"ok": result.ok, "dry_run": False, "transport": result.safe_summary})


@app.post("/reply", response_model=None)
def reply(
    request: ReplyRequest,
    authorization: str | None = Header(default=None),
    x_nexus_upstream_token: str | None = Header(default=None),
):
    settings = _load_settings()
    _assert_authorized(settings, authorization, x_nexus_upstream_token)
    if settings.mode == "disabled":
        return _safe_error(503, "upstream_adapter_disabled")
    if settings.mode == "contract_fixture":
        return _normalize_strict_reply(_fixture_reply(request))
    if settings.mode == "codex_app_server":
        if not _auth_discovery(settings)["selected"]["usable"]:
            return _safe_error(503, "codex_auth_source_missing")
        if not settings.app_server_reply_enabled:
            return _safe_error(503, "codex_app_server_reply_transport_disabled")
        result = asyncio.run(
            post_reply_turn(
                settings=ReplyTransportSettings(
                    app_server_base_url=settings.app_server_base_url,
                    reply_path=settings.app_server_reply_path,
                    timeout_ms=settings.app_server_timeout_ms,
                    allow_public_url=settings.app_server_allow_public_url,
                    bearer_token=settings.app_server_reply_token,
                ),
                reply_payload=request.model_dump(),
            )
        )
        if not result.ok:
            return JSONResponse(status_code=502, content={"ok": False, "error_code": result.error_code or "app_server_reply_failed", "transport": result.safe_summary})
        try:
            return _normalize_strict_reply(result.response_payload)
        except FastReplyParseError:
            return JSONResponse(status_code=502, content={"ok": False, "error_code": "upstream_invalid_fast_reply", "transport": result.safe_summary})
    return _safe_error(503, "upstream_adapter_disabled")


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("CODEX_UPSTREAM_ADAPTER_HOST") or "127.0.0.1"
    port = int(os.getenv("CODEX_UPSTREAM_ADAPTER_PORT") or "18794")
    uvicorn.run("upstream_adapter:app", host=host, port=port, reload=False)
