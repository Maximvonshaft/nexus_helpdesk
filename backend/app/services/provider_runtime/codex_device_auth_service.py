from __future__ import annotations

import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session


_OPENCLAW_DEVICE_POLL_MODES = {"openclaw", "openclaw_codex", "device_auth", "device_auth_id"}
_GENERIC_DEVICE_POLL_MODES = {"generic", "generic_device_code", "device_code", "oauth_device_code"}
_ALLOWED_DEVICE_POLL_MODES = {"auto", *_OPENCLAW_DEVICE_POLL_MODES, *_GENERIC_DEVICE_POLL_MODES}
_OPENCLAW_DEVICE_TOKEN_HINT = "deviceauth/token"
_OPENCLAW_DEVICE_CALLBACK_PATH = "/deviceauth/callback"


class CodexDeviceAuthService:
    def __init__(self, db: Session, crypto_service):
        self.db = db
        self.crypto_service = crypto_service
        self.enabled = os.environ.get("CODEX_OAUTH_DEVICE_FLOW_ENABLED", "false").lower() == "true"
        self.auth_base_url = os.environ.get("CODEX_OAUTH_AUTH_BASE_URL", "").rstrip("/")
        self.client_id = os.environ.get("CODEX_OAUTH_CLIENT_ID", "").strip()
        self.usercode_path = os.environ.get("CODEX_OAUTH_DEVICE_USERCODE_PATH", "").strip()
        self.device_token_path = os.environ.get("CODEX_OAUTH_DEVICE_TOKEN_PATH", "").strip()
        self.token_path = (os.environ.get("CODEX_OAUTH_TOKEN_URL") or os.environ.get("CODEX_OAUTH_TOKEN_PATH", "")).strip()
        self.device_poll_payload_mode = os.environ.get("CODEX_OAUTH_DEVICE_POLL_PAYLOAD_MODE", "auto").strip().lower() or "auto"
        self.device_redirect_uri = os.environ.get("CODEX_OAUTH_DEVICE_REDIRECT_URI", "").strip()

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise ValueError("Device flow is disabled")
        missing = []
        if not self.auth_base_url:
            missing.append("CODEX_OAUTH_AUTH_BASE_URL")
        if not self.client_id:
            missing.append("CODEX_OAUTH_CLIENT_ID")
        if not self.usercode_path:
            missing.append("CODEX_OAUTH_DEVICE_USERCODE_PATH")
        if not self.device_token_path:
            missing.append("CODEX_OAUTH_DEVICE_TOKEN_PATH")
        if not self.token_path:
            missing.append("CODEX_OAUTH_TOKEN_URL or CODEX_OAUTH_TOKEN_PATH")
        if missing:
            raise ValueError("Codex device flow missing configuration: " + ", ".join(missing))
        if self.device_poll_payload_mode not in _ALLOWED_DEVICE_POLL_MODES:
            raise ValueError(
                "CODEX_OAUTH_DEVICE_POLL_PAYLOAD_MODE must be auto, openclaw, or generic_device_code"
            )

    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url
        return f"{self.auth_base_url}/{path_or_url.lstrip('/')}"

    def _use_openclaw_device_poll(self) -> bool:
        mode = self.device_poll_payload_mode
        if mode in _OPENCLAW_DEVICE_POLL_MODES:
            return True
        if mode in _GENERIC_DEVICE_POLL_MODES:
            return False
        token_path = self.device_token_path.lower()
        auth_base = self.auth_base_url.lower()
        return _OPENCLAW_DEVICE_TOKEN_HINT in token_path or "auth.openai.com" in auth_base

    def _device_exchange_redirect_uri(self) -> str | None:
        if self.device_redirect_uri:
            return self.device_redirect_uri
        if self._use_openclaw_device_poll() and self.auth_base_url:
            return f"{self.auth_base_url}{_OPENCLAW_DEVICE_CALLBACK_PATH}"
        return None

    def _build_device_poll_payload(self, session: dict[str, Any]) -> dict[str, Any]:
        device_auth_id = session.get("device_auth_id")
        if not device_auth_id:
            raise ValueError("Codex device auth session is missing device_auth_id")
        if self._use_openclaw_device_poll():
            user_code = session.get("user_code")
            if not user_code:
                raise ValueError("OpenClaw-compatible Codex device auth polling requires user_code")
            return {
                "device_auth_id": device_auth_id,
                "user_code": user_code,
            }
        return {
            "client_id": self.client_id,
            "device_code": device_auth_id,
        }

    def _is_pending_poll_response(self, response: httpx.Response, error_code: str | None) -> bool:
        if response.status_code in {400, 401} and error_code in {"authorization_pending", "slow_down"}:
            return True
        if self._use_openclaw_device_poll() and response.status_code in {403, 404}:
            return True
        return False

    async def start_device_flow(self, tenant_id: str, user_id: str, scope: str | None = None) -> dict[str, Any]:
        self._require_enabled()
        payload = {"client_id": self.client_id}
        if scope:
            payload["scope"] = scope
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.post(
                self._url(self.usercode_path),
                json=payload,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()

        user_code = data.get("user_code") or data.get("userCode") or data.get("usercode")
        verification_url = data.get("verification_uri") or data.get("verification_url") or data.get("verificationUrl")
        device_auth_id = data.get("device_code") or data.get("device_auth_id") or data.get("deviceAuthId")
        interval = int(data.get("interval") or 5)
        expires_in = int(data.get("expires_in") or 900)
        if not user_code or not verification_url or not device_auth_id:
            raise ValueError("Codex device flow response missing required fields")

        session_id = str(uuid.uuid4())
        state = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        self.db.execute(text("""
            INSERT INTO provider_auth_sessions
            (id, tenant_id, provider, flow_type, state, device_auth_id, user_code, verification_url,
             scope, expires_at, status, created_by)
            VALUES (:id, :tenant_id, 'openai-codex', 'device_code', :state, :device_auth_id, :user_code, :verification_url,
                    :scope, :expires_at, 'pending', :created_by)
        """), {
            "id": session_id,
            "tenant_id": tenant_id,
            "state": state,
            "device_auth_id": device_auth_id,
            "user_code": user_code,
            "verification_url": verification_url,
            "scope": scope,
            "expires_at": expires_at,
            "created_by": user_id,
        })
        self.db.commit()
        return {
            "session_id": session_id,
            "verification_url": verification_url,
            "user_code": user_code,
            "expires_at": expires_at.isoformat(),
            "interval": interval,
            "scope": scope,
        }

    async def status_device_flow(self, tenant_id: str, session_id: str) -> dict[str, Any]:
        row = self.db.execute(text("""
            SELECT status, expires_at, error_code
            FROM provider_auth_sessions
            WHERE id = :id AND tenant_id = :tenant_id
        """), {"id": session_id, "tenant_id": tenant_id}).mappings().first()
        if not row:
            return {"status": "not_found"}
        expires_at = row["expires_at"]
        if expires_at and getattr(expires_at, "tzinfo", None) is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if row["status"] == "pending" and expires_at and expires_at < datetime.now(timezone.utc):
            self.db.execute(text("UPDATE provider_auth_sessions SET status = 'expired', error_code = 'expired' WHERE id = :id AND tenant_id = :tenant_id"), {"id": session_id, "tenant_id": tenant_id})
            self.db.commit()
            return {"status": "expired", "error_code": "expired"}
        return {"status": row["status"], "error_code": row["error_code"]}

    async def poll_device_flow(self, tenant_id: str, session_id: str) -> dict[str, Any]:
        self._require_enabled()
        session = self.db.execute(text("""
            SELECT id, device_auth_id, user_code, status, expires_at, created_by, scope
            FROM provider_auth_sessions
            WHERE id = :id AND tenant_id = :tenant_id
        """), {"id": session_id, "tenant_id": tenant_id}).mappings().first()
        if not session:
            return {"status": "not_found"}
        if session["status"] != "pending":
            return {"status": session["status"]}

        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.post(
                self._url(self.device_token_path),
                json=self._build_device_poll_payload(dict(session)),
                headers={"Accept": "application/json"},
            )
            data = _safe_response_json(response)
            error_code = data.get("error") or data.get("error_code") or "authorization_pending"
            if self._is_pending_poll_response(response, error_code):
                return {"status": "pending", "error_code": error_code}
            if response.status_code >= 400:
                self._mark_session(session_id=session_id, tenant_id=tenant_id, status="failed", error_code=error_code)
                return {"status": "failed", "error_code": error_code}
            response.raise_for_status()

        token_data = await self._exchange_token(data)
        access_token = token_data.get("access_token") or token_data.get("access")
        refresh_token = token_data.get("refresh_token") or token_data.get("refresh")
        expires_in = int(token_data.get("expires_in") or token_data.get("expires") or 3600)
        if not access_token:
            self._mark_session(session_id=session_id, tenant_id=tenant_id, status="failed", error_code="token_exchange_missing_access")
            return {"status": "failed", "error_code": "token_exchange_missing_access"}

        credential_id = self._upsert_credential(
            tenant_id=tenant_id,
            created_by=str(session["created_by"] or ""),
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
            account_id=token_data.get("account_id") or token_data.get("accountId"),
            email=token_data.get("email"),
            plan_type=token_data.get("chatgpt_plan_type") or token_data.get("chatgptPlanType"),
            scope=token_data.get("scope") or session["scope"],
        )
        self._mark_session(session_id=session_id, tenant_id=tenant_id, status="authorized", error_code=None)
        return {"status": "authorized", "credential_id": credential_id}

    async def _exchange_token(self, device_token_data: dict[str, Any]) -> dict[str, Any]:
        if device_token_data.get("access_token") or device_token_data.get("access"):
            return device_token_data
        authorization_code = device_token_data.get("authorization_code") or device_token_data.get("code")
        code_verifier = device_token_data.get("code_verifier") or device_token_data.get("codeVerifier")
        if not authorization_code:
            return device_token_data
        payload = {
            "grant_type": "authorization_code",
            "code": authorization_code,
            "client_id": self.client_id,
        }
        redirect_uri = self._device_exchange_redirect_uri()
        if redirect_uri:
            payload["redirect_uri"] = redirect_uri
        if code_verifier:
            payload["code_verifier"] = code_verifier
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.post(self._url(self.token_path), data=payload, headers={"Accept": "application/json"})
            response.raise_for_status()
            return response.json()

    def _upsert_credential(self, *, tenant_id: str, created_by: str, access_token: str, refresh_token: str | None, expires_in: int, account_id: str | None, email: str | None, plan_type: str | None, scope: str | None = None) -> str:
        credential_id = str(uuid.uuid4())
        profile_id = account_id or email or credential_id
        encrypted_access = self.crypto_service.encrypt(access_token)
        encrypted_refresh = self.crypto_service.encrypt(refresh_token) if refresh_token else None
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        fingerprint = self.crypto_service.get_safe_fingerprint("openai-codex", tenant_id, profile_id, access_token)
        row = self.db.execute(text("""
            INSERT INTO provider_credentials
            (id, tenant_id, provider, provider_runtime, credential_type, profile_id, account_id, email, chatgpt_plan_type,
             encrypted_access_token, encrypted_refresh_token, expires_at, status, token_fingerprint, created_by, scope)
            VALUES
            (:id, :tenant_id, 'openai-codex', 'codex_app_server', 'oauth', :profile_id, :account_id, :email, :plan_type,
             :access, :refresh, :expires_at, 'active', :fingerprint, :created_by, :scope)
            ON CONFLICT (tenant_id, provider, profile_id) WHERE revoked_at IS NULL
            DO UPDATE SET encrypted_access_token = EXCLUDED.encrypted_access_token,
                          encrypted_refresh_token = EXCLUDED.encrypted_refresh_token,
                          expires_at = EXCLUDED.expires_at,
                          scope = EXCLUDED.scope,
                          status = 'active',
                          last_error_code = NULL,
                          updated_at = now()
            RETURNING id
        """), {
            "id": credential_id,
            "tenant_id": tenant_id,
            "profile_id": profile_id,
            "account_id": account_id,
            "email": email,
            "plan_type": plan_type,
            "access": encrypted_access,
            "refresh": encrypted_refresh,
            "expires_at": expires_at,
            "fingerprint": fingerprint,
            "created_by": created_by,
            "scope": scope,
        }).first()
        self.db.commit()
        return str(row[0]) if row else credential_id

    def _mark_session(self, *, session_id: str, tenant_id: str, status: str, error_code: str | None) -> None:
        completed = datetime.now(timezone.utc) if status in {"authorized", "failed", "expired", "cancelled"} else None
        self.db.execute(text("""
            UPDATE provider_auth_sessions
            SET status = :status, error_code = :error_code, completed_at = :completed
            WHERE id = :id AND tenant_id = :tenant_id
        """), {"status": status, "error_code": error_code, "completed": completed, "id": session_id, "tenant_id": tenant_id})
        self.db.commit()


def _safe_response_json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
