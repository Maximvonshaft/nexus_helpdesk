#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.webchat_fast_output_parser import parse_openclaw_fast_reply

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from upstream_auth_discovery import (  # noqa: E402
    auth_source_public_summary,
    discover_auth_sources,
    select_best_auth_source,
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


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


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
    return UpstreamAdapterSettings(
        mode=mode,
        app_env=(os.getenv("APP_ENV") or "development").strip().lower(),
        require_auth=_env_bool("CODEX_UPSTREAM_ADAPTER_REQUIRE_AUTH", True),
        shared_token=shared_token,
        auth_profile_file=(os.getenv("CODEX_UPSTREAM_AUTH_PROFILE_FILE") or "").strip() or None,
        codex_cli_auth_file=(os.getenv("CODEX_CLI_AUTH_FILE") or "").strip() or None,
        api_key_file=(os.getenv("CODEX_UPSTREAM_API_KEY_FILE") or "").strip() or None,
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


def _normalize_strict_reply(payload: dict[str, Any]) -> dict[str, Any]:
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
    return {"ok": True, "mode": settings.mode, "auth_required": settings.require_auth}


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
        "boundary": {
            "browser_cookie_scraping": False,
            "chatgpt_session_scraping": False,
            "shell_execution": False,
            "file_write": False,
            "tool_execution": False,
        },
    }


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
        return _safe_error(501, "codex_app_server_transport_not_implemented")
    return _safe_error(503, "upstream_adapter_disabled")


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("CODEX_UPSTREAM_ADAPTER_HOST") or "127.0.0.1"
    port = int(os.getenv("CODEX_UPSTREAM_ADAPTER_PORT") or "18794")
    uvicorn.run("upstream_adapter:app", host=host, port=port, reload=False)
