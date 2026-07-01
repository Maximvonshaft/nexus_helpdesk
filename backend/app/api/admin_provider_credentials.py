from __future__ import annotations

from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import UserRole
from ..services.permissions import ensure_can_manage_runtime
from ..services.provider_runtime.adapters.codex_direct import CodexDirectAdapter
from ..services.provider_runtime.codex_credential_broker import CodexCredentialBroker
from ..services.provider_runtime.codex_device_auth_service import CodexDeviceAuthService
from ..services.provider_runtime.codex_oauth_config import CodexOAuthConfig, resolve_provider_tenant_id
from ..services.provider_runtime.codex_smoke_chat import CodexSmokeChatError, CodexSmokeChatRequest, CodexSmokeChatService
from ..services.provider_runtime.credential_crypto import CredentialCryptoService
from .deps import get_current_user


router = APIRouter(prefix="/api/admin/provider-credentials", tags=["admin-provider-credentials"])


class CodexScopeRequest(BaseModel):
    scopes: list[str] | None = Field(default=None, max_length=50)


class CodexManualCompleteRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=120)
    authorization_response: str = Field(min_length=1, max_length=4096)


class CodexCredentialActionResponse(BaseModel):
    ok: bool
    status: str
    credential_id: str | None = None
    error_code: str | None = None
    upstream_revoke: str | None = None


class CodexSmokeChatRequestBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=1000)
    nonce: str | None = Field(default=None, max_length=120)
    mode: Literal["smoke"] = "smoke"


def _broker(db: Session) -> CodexCredentialBroker:
    return CodexCredentialBroker(db, CredentialCryptoService(), CodexOAuthConfig.from_env())


def _device_service(db: Session) -> CodexDeviceAuthService:
    return CodexDeviceAuthService(db, CredentialCryptoService())


def _tenant_id(current_user) -> str:
    return resolve_provider_tenant_id(current_user)


def _ensure_manage(current_user, db: Session) -> None:
    ensure_can_manage_runtime(current_user, db)


def _ensure_admin_manage(current_user, db: Session) -> None:
    if current_user.role != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    ensure_can_manage_runtime(current_user, db)


@router.get("/codex/status")
def codex_credentials_status(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _ensure_manage(current_user, db)
    return _broker(db).list_credentials(tenant_id=_tenant_id(current_user))


@router.post("/codex/direct-smoke")
async def codex_direct_smoke(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _ensure_admin_manage(current_user, db)
    # Readiness-only smoke. It never reads or returns Codex auth material; it
    # checks binary presence, HOME/.codex/auth.json presence, login status, and
    # production sandbox acknowledgement.
    return await CodexDirectAdapter().smoke_check()


@router.post("/codex/smoke-chat")
async def codex_smoke_chat(payload: CodexSmokeChatRequestBody, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _ensure_admin_manage(current_user, db)
    try:
        return await CodexSmokeChatService(db).smoke_chat(
            CodexSmokeChatRequest(
                prompt=payload.prompt,
                nonce=payload.nonce,
                actor_id=current_user.id,
                tenant_id=_tenant_id(current_user),
            )
        )
    except CodexSmokeChatError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "ok": False,
                "provider": "codex",
                "credential_status": exc.credential_status,
                "model_call_status": "failed",
                "reason": exc.reason,
                "request_id": exc.request_id,
            },
        ) from exc


@router.post("/codex/authorize")
def start_codex_authorization(payload: CodexScopeRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _ensure_manage(current_user, db)
    try:
        return _broker(db).start_authorization_code_flow(
            tenant_id=_tenant_id(current_user),
            user_id=str(current_user.id),
            requested_scopes=payload.scopes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.post("/codex/manual/start")
def start_codex_manual_authorization(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _ensure_manage(current_user, db)
    return _broker(db).start_external_channel_manual_paste_flow(
        tenant_id=_tenant_id(current_user),
        user_id=str(current_user.id),
    )


@router.post("/codex/manual/complete")
async def complete_codex_manual_authorization(
    payload: CodexManualCompleteRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_manage(current_user, db)
    try:
        return await _broker(db).complete_external_channel_manual_paste_flow(
            tenant_id=_tenant_id(current_user),
            session_id=payload.session_id,
            authorization_response=payload.authorization_response,
            actor_id=str(current_user.id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/codex/callback")
async def codex_authorization_callback(
    request: Request,
    state: str | None = Query(default=None),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    # This endpoint is intentionally authenticated by the high-entropy OAuth
    # state rather than the Nexus bearer token, because external providers call
    # it through a browser redirect. It never returns or logs token values.
    try:
        result = await _broker(db).complete_authorization_code_callback(
            state=state,
            code=code,
            error=error,
            error_description=error_description,
        )
    except ValueError as exc:
        result = {"ok": False, "status": "failed", "error_code": "callback_misconfigured", "detail": str(exc)}

    config = CodexOAuthConfig.from_env()
    redirect_base = config.success_redirect_url if result.get("ok") else config.failure_redirect_url
    if redirect_base:
        query = urlencode({
            "status": result.get("status", "failed"),
            "provider": "openai-codex",
            "error_code": result.get("error_code") or "",
        })
        separator = "&" if "?" in redirect_base else "?"
        return RedirectResponse(f"{redirect_base}{separator}{query}", status_code=303)
    status_code = 200 if result.get("ok") else 400
    return JSONResponse(status_code=status_code, content=result)


@router.post("/codex/device/start")
async def start_codex_device_flow(payload: CodexScopeRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _ensure_manage(current_user, db)
    config = CodexOAuthConfig.from_env()
    try:
        scope = config.normalize_scope(payload.scopes)
        return await _device_service(db).start_device_flow(_tenant_id(current_user), str(current_user.id), scope=scope)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.get("/codex/device/status/{session_id}")
def codex_device_flow_status(session_id: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _ensure_manage(current_user, db)
    return _broker(db).get_session_status(tenant_id=_tenant_id(current_user), session_id=session_id)


@router.post("/codex/device/poll/{session_id}")
async def poll_codex_device_flow(session_id: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _ensure_manage(current_user, db)
    return await _device_service(db).poll_device_flow(_tenant_id(current_user), session_id)


@router.post("/codex/refresh/{credential_id}", response_model=CodexCredentialActionResponse)
async def refresh_codex_credential(credential_id: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _ensure_manage(current_user, db)
    return await _broker(db).refresh_credential(tenant_id=_tenant_id(current_user), credential_id=credential_id, actor_id=current_user.id)


@router.post("/codex/revoke/{credential_id}", response_model=CodexCredentialActionResponse)
async def revoke_codex_credential(credential_id: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _ensure_manage(current_user, db)
    return await _broker(db).revoke_credential(tenant_id=_tenant_id(current_user), credential_id=credential_id, actor_id=current_user.id)


@router.post("/codex/disconnect/{credential_id}", response_model=CodexCredentialActionResponse)
def disconnect_codex_credential(credential_id: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _ensure_manage(current_user, db)
    return _broker(db).disconnect_credential(tenant_id=_tenant_id(current_user), credential_id=credential_id, actor_id=current_user.id)
