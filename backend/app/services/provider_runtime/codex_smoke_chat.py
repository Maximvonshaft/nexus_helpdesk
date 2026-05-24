from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..audit_service import log_admin_audit
from .codex_llm_client import (
    CodexLLMClient,
    CodexLLMCredential,
    CodexLLMCredentialRefreshRequired,
    CodexLLMEndpointNotConfigured,
    CodexLLMProviderCallFailed,
)
from .codex_oauth_config import CODEX_PROVIDER
from .credential_crypto import CredentialCryptoService

logger = logging.getLogger(__name__)


class CodexSmokeChatError(RuntimeError):
    def __init__(self, status_code: int, reason: str, *, credential_status: str = "unknown", request_id: str | None = None) -> None:
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason
        self.credential_status = credential_status
        self.request_id = request_id or str(uuid.uuid4())


@dataclass(frozen=True)
class CodexSmokeChatRequest:
    prompt: str
    nonce: str | None
    actor_id: int | None
    tenant_id: str


class CodexSmokeChatService:
    """Admin-only Code X smoke caller.

    The service uses the existing provider credential row and OAuthRefreshManager
    to obtain a valid access token. It never returns or audits the token value.
    """

    provider = "codex"

    def __init__(self, db: Session, crypto_service: CredentialCryptoService | None = None):
        self.db = db
        self.crypto_service = crypto_service or CredentialCryptoService()

    async def smoke_chat(self, request: CodexSmokeChatRequest) -> dict[str, Any]:
        started = time.monotonic()
        request_id = str(uuid.uuid4())
        nonce = (request.nonce or uuid.uuid4().hex).strip()
        credential = self._read_active_credential(request.tenant_id)
        if not credential:
            self._audit(request, request_id, "credential_not_found", 0, nonce=nonce, model_call_status="not_started")
            raise CodexSmokeChatError(404, "codex_credential_not_found", credential_status="not_found", request_id=request_id)

        codex_credential = CodexLLMCredential(
            id=credential["id"],
            tenant_id=credential["tenant_id"],
            account_id=credential.get("account_id"),
            chatgpt_plan_type=credential.get("chatgpt_plan_type"),
        )
        try:
            result = await CodexLLMClient(self.db, self.crypto_service).call_codex_smoke_chat(
                prompt=request.prompt,
                nonce=nonce,
                credential=codex_credential,
                request_id=request_id,
            )
        except CodexLLMEndpointNotConfigured as exc:
            elapsed_ms = _elapsed_ms(started)
            self._audit(request, request_id, "codex_llm_endpoint_not_configured", elapsed_ms, nonce=nonce, model_call_status="not_started", credential_id=credential["id"])
            raise CodexSmokeChatError(503, "codex_llm_endpoint_not_configured", credential_status="authorized", request_id=request_id) from exc
        except CodexLLMCredentialRefreshRequired as exc:
            elapsed_ms = _elapsed_ms(started)
            self._audit(request, request_id, "credential_refresh_required", elapsed_ms, nonce=nonce, model_call_status="not_started", credential_id=credential["id"])
            raise CodexSmokeChatError(409, "credential_refresh_required", credential_status="refresh_required", request_id=request_id) from exc
        except CodexLLMProviderCallFailed as exc:
            elapsed_ms = _elapsed_ms(started)
            logger.warning(
                "codex_smoke_chat_provider_failed",
                extra={"request_id": request_id, "provider": self.provider, "error_type": type(exc).__name__},
            )
            self._audit(request, request_id, "codex_provider_call_failed", elapsed_ms, nonce=nonce, model_call_status="failed", credential_id=credential["id"])
            raise CodexSmokeChatError(502, "codex_provider_call_failed", credential_status="authorized", request_id=request_id) from exc

        elapsed_ms = _elapsed_ms(started)
        nonce_echoed = nonce in result.response_text
        self._mark_credential_used(request.tenant_id, credential["id"])
        self._audit(
            request,
            request_id,
            "completed",
            elapsed_ms,
            nonce=nonce,
            model_call_status="completed",
            credential_id=credential["id"],
            provider_status=result.provider_status,
            nonce_echoed=nonce_echoed,
            api_style=result.api_style,
        )
        self.db.commit()
        return {
            "ok": True,
            "provider": self.provider,
            "credential_status": "authorized",
            "model_call_status": "completed",
            "nonce_echoed": nonce_echoed,
            "response_text_redacted": _redact_text(result.response_text),
            "latency_ms": elapsed_ms,
            "request_id": request_id,
            "warnings": [],
        }

    def _read_active_credential(self, tenant_id: str):
        return self.db.execute(text("""
            SELECT id, tenant_id, account_id, chatgpt_plan_type, expires_at
            FROM provider_credentials
            WHERE tenant_id = :tenant_id
              AND provider = :provider
              AND provider_runtime = 'codex_app_server'
              AND credential_type = 'oauth'
              AND status = 'active'
              AND revoked_at IS NULL
            ORDER BY created_at DESC
            LIMIT 1
        """), {"tenant_id": tenant_id, "provider": CODEX_PROVIDER}).mappings().first()

    def _mark_credential_used(self, tenant_id: str, credential_id: str) -> None:
        self.db.execute(text("""
            UPDATE provider_credentials
            SET last_used_at = :now, last_error_code = NULL, updated_at = :now
            WHERE tenant_id = :tenant_id AND id = :id AND provider = :provider
        """), {"tenant_id": tenant_id, "id": credential_id, "provider": CODEX_PROVIDER, "now": datetime.now(timezone.utc)})

    def _audit(
        self,
        request: CodexSmokeChatRequest,
        request_id: str,
        status: str,
        elapsed_ms: int,
        *,
        nonce: str,
        model_call_status: str,
        credential_id: str | None = None,
        provider_status: int | None = None,
        nonce_echoed: bool | None = None,
        api_style: str | None = None,
    ) -> None:
        try:
            log_admin_audit(
                self.db,
                actor_id=request.actor_id,
                action="codex_smoke_chat_invoked",
                target_type="provider_credential",
                target_id=None,
                new_value={
                    "provider": self.provider,
                    "credential_id_hash": _hash_value(credential_id),
                    "request_id": request_id,
                    "prompt_hash": _hash_value(request.prompt),
                    "prompt_length": len(request.prompt),
                    "nonce_hash": _hash_value(nonce),
                    "status": status,
                    "provider_status": provider_status,
                    "model_call_status": model_call_status,
                    "nonce_echoed": nonce_echoed,
                    "api_style": api_style,
                    "latency_ms": elapsed_ms,
                },
            )
            self.db.commit()
        except Exception as exc:
            logger.warning("codex_smoke_chat_audit_failed", extra={"request_id": request_id, "error_type": type(exc).__name__})
            self.db.rollback()

def _hash_value(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def _redact_text(value: str) -> str:
    text_value = value[:2000]
    replacements = [
        ("access_token", "access_[redacted]"),
        ("refresh_token", "refresh_[redacted]"),
        ("authorization", "auth_[redacted]"),
        ("client_secret", "client_[redacted]"),
    ]
    for needle, replacement in replacements:
        text_value = text_value.replace(needle, replacement).replace(needle.upper(), replacement)
    text_value = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]{8,}", "Bearer [redacted]", text_value, flags=re.IGNORECASE)
    text_value = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[redacted_api_key]", text_value)
    text_value = re.sub(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b", "[redacted_jwt]", text_value)
    return text_value


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
