#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.webchat_fast_output_parser import FastReplyParseError, parse_openclaw_fast_reply


BridgeMode = Literal["disabled", "stub", "upstream"]


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


class StrictReply(BaseModel):
    reply: str
    intent: str
    tracking_number: str | None
    handoff_required: bool
    handoff_reason: str | None
    recommended_agent_action: str | None


@dataclass(frozen=True)
class BridgeSettings:
    mode: BridgeMode
    app_env: str
    require_auth: bool
    shared_token: str | None
    upstream_url: str | None
    upstream_token: str | None
    upstream_timeout_ms: int
    allow_stub_in_production: bool


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


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


def _load_settings() -> BridgeSettings:
    raw_mode = (os.getenv("CODEX_REPLY_BRIDGE_MODE") or "disabled").strip().lower()
    mode: BridgeMode = raw_mode if raw_mode in {"disabled", "stub", "upstream"} else "disabled"  # type: ignore[assignment]
    shared_token = _read_secret_file(os.getenv("CODEX_REPLY_BRIDGE_SHARED_TOKEN_FILE"))
    shared_token = shared_token or (os.getenv("CODEX_REPLY_BRIDGE_SHARED_TOKEN") or "").strip() or None
    upstream_token = _read_secret_file(os.getenv("CODEX_REPLY_BRIDGE_UPSTREAM_TOKEN_FILE"))
    upstream_token = upstream_token or (os.getenv("CODEX_REPLY_BRIDGE_UPSTREAM_TOKEN") or "").strip() or None
    return BridgeSettings(
        mode=mode,
        app_env=(os.getenv("APP_ENV") or "development").strip().lower(),
        require_auth=_env_bool("CODEX_REPLY_BRIDGE_REQUIRE_AUTH", True),
        shared_token=shared_token,
        upstream_url=(os.getenv("CODEX_REPLY_BRIDGE_UPSTREAM_URL") or "").strip() or None,
        upstream_token=upstream_token,
        upstream_timeout_ms=_env_int("CODEX_REPLY_BRIDGE_UPSTREAM_TIMEOUT_MS", 15000),
        allow_stub_in_production=_env_bool("CODEX_REPLY_BRIDGE_ALLOW_STUB_IN_PRODUCTION", False),
    )


def _safe_error(status_code: int, error_code: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"ok": False, "error_code": error_code})


def _auth_token_from_headers(authorization: str | None, x_nexus_bridge_token: str | None) -> str | None:
    if x_nexus_bridge_token and x_nexus_bridge_token.strip():
        return x_nexus_bridge_token.strip()
    if not authorization:
        return None
    value = authorization.strip()
    if value.lower().startswith("bearer "):
        return value.split(None, 1)[1].strip()
    return None


def _assert_authorized(
    settings: BridgeSettings,
    authorization: str | None,
    x_nexus_bridge_token: str | None,
) -> None:
    if not settings.require_auth:
        return
    if not settings.shared_token:
        raise HTTPException(status_code=503, detail="bridge_auth_not_configured")
    supplied = _auth_token_from_headers(authorization, x_nexus_bridge_token)
    if not supplied or supplied != settings.shared_token:
        raise HTTPException(status_code=401, detail="bridge_auth_failed")


def _stub_reply(request: ReplyRequest) -> dict[str, Any]:
    body = request.body.strip()
    has_tracking_fact = bool(request.tracking_fact_summary and request.tracking_fact_evidence_present)
    if has_tracking_fact:
        reply = "I found the available parcel information. Please check the latest tracking details in your shipment page, and contact our support team if anything looks incorrect."
        intent = "tracking"
    elif any(char.isdigit() for char in body):
        reply = "Thanks. I will need a confirmed tracking result before giving a parcel status. Please wait while our support team checks it."
        intent = "tracking_unresolved"
    else:
        reply = "Please share your tracking number so I can check your parcel status."
        intent = "tracking_missing_number"
    return {
        "reply": reply,
        "intent": intent,
        "tracking_number": None,
        "handoff_required": False,
        "handoff_reason": None,
        "recommended_agent_action": None,
    }


def _post_upstream(settings: BridgeSettings, payload: dict[str, Any]) -> Any:
    if not settings.upstream_url:
        raise RuntimeError("upstream_url_not_configured")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Nexus-Bridge": "codex-reply-sidecar-v1",
    }
    if settings.upstream_token:
        headers["Authorization"] = f"Bearer {settings.upstream_token}"
    req = urllib.request.Request(
        settings.upstream_url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=settings.upstream_timeout_ms / 1000) as response:  # noqa: S310 - private operator configured bridge.
        text = response.read().decode("utf-8", errors="replace").strip()
        if not text:
            return ""
        try:
            return json.loads(text)
        except ValueError:
            return text


def _normalize_strict_reply(payload: Any) -> dict[str, Any]:
    parsed = parse_openclaw_fast_reply(payload)
    return StrictReply(
        reply=parsed.reply,
        intent=parsed.intent,
        tracking_number=parsed.tracking_number,
        handoff_required=parsed.handoff_required,
        handoff_reason=parsed.handoff_reason,
        recommended_agent_action=parsed.recommended_agent_action,
    ).model_dump()


app = FastAPI(title="NexusDesk Codex Reply Bridge", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "service": "codex-reply-bridge"}


@app.get("/readyz", response_model=None)
def readyz():
    settings = _load_settings()
    if settings.mode == "disabled":
        return _safe_error(503, "bridge_disabled")
    if settings.mode == "stub" and settings.app_env == "production" and not settings.allow_stub_in_production:
        return _safe_error(503, "stub_forbidden_in_production")
    if settings.mode == "upstream" and not settings.upstream_url:
        return _safe_error(503, "upstream_url_not_configured")
    if settings.require_auth and not settings.shared_token:
        return _safe_error(503, "bridge_auth_not_configured")
    return {"ok": True, "mode": settings.mode, "auth_required": settings.require_auth}


@app.get("/auth/status")
def auth_status(authorization: str | None = Header(default=None), x_nexus_bridge_token: str | None = Header(default=None)) -> dict[str, Any]:
    settings = _load_settings()
    supplied = _auth_token_from_headers(authorization, x_nexus_bridge_token)
    return {
        "ok": True,
        "auth_required": settings.require_auth,
        "shared_token_configured": bool(settings.shared_token),
        "request_token_present": bool(supplied),
        "mode": settings.mode,
    }


@app.post("/reply", response_model=None)
async def reply(
    request: ReplyRequest,
    authorization: str | None = Header(default=None),
    x_nexus_bridge_token: str | None = Header(default=None),
):
    settings = _load_settings()
    started = time.monotonic()
    _assert_authorized(settings, authorization, x_nexus_bridge_token)

    if settings.mode == "disabled":
        return _safe_error(503, "bridge_disabled")
    if settings.mode == "stub" and settings.app_env == "production" and not settings.allow_stub_in_production:
        return _safe_error(503, "stub_forbidden_in_production")

    try:
        if settings.mode == "stub":
            candidate = _stub_reply(request)
        elif settings.mode == "upstream":
            candidate = _post_upstream(settings, request.model_dump())
        else:
            return _safe_error(503, "bridge_disabled")
        strict_reply = _normalize_strict_reply(candidate)
    except FastReplyParseError:
        return _safe_error(502, "upstream_invalid_fast_reply")
    except urllib.error.HTTPError as exc:
        return _safe_error(502 if exc.code >= 500 else exc.code, "upstream_http_error")
    except Exception:
        return _safe_error(502, "upstream_unavailable")

    elapsed_ms = int((time.monotonic() - started) * 1000)
    if _env_bool("CODEX_REPLY_BRIDGE_LOG_SUCCESS", False):
        print(
            json.dumps(
                {
                    "event": "codex_reply_bridge.success",
                    "mode": settings.mode,
                    "elapsed_ms": elapsed_ms,
                    "request_id": request.request_id,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
    return strict_reply


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("CODEX_REPLY_BRIDGE_HOST") or "127.0.0.1"
    port = int(os.getenv("CODEX_REPLY_BRIDGE_PORT") or "18793")
    uvicorn.run("sidecar:app", host=host, port=port, reload=False)
