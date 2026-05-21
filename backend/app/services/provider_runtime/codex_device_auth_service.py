from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session


class CodexDeviceAuthService:
    def __init__(self, db: Session, crypto_service):
        self.db = db
        self.crypto_service = crypto_service
        self.enabled = os.environ.get("CODEX_OAUTH_DEVICE_FLOW_ENABLED", "false").lower() == "true"
        self.auth_base_url = os.environ.get("CODEX_OAUTH_AUTH_BASE_URL", "").rstrip("/")
        self.client_id = os.environ.get("CODEX_OAUTH_CLIENT_ID", "").strip()
        self.usercode_path = os.environ.get("CODEX_OAUTH_DEVICE_USERCODE_PATH", "").strip()
        self.device_token_path = os.environ.get("CODEX_OAUTH_DEVICE_TOKEN_PATH", "").strip()
        self.token_path = os.environ.get("CODEX_OAUTH_TOKEN_PATH", "").strip()

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
            missing.append("CODEX_OAUTH_TOKEN_PATH")
        if missing:
            raise ValueError("Codex device flow missing configuration: " + ", ".join(missing))

    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url
        return f"{self.auth_base_url}/{path_or_url.lstrip('/')}"

    async def start_device_flow(self, tenant_id: str, user_id: str) -> dict[str, Any]:
        self._require_enabled()
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self._url(self.usercode_path),
                json={"client_id": self.client_id},
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()

        user_code = data.get("user_code") or data.get("userCode")
        verification_url = data.get("verification_uri") or data.get("verification_url") or data.get("verificationUrl")
        device_auth_id = data.get("device_code") or data.get("device_auth_id") or data.get("deviceAuthId")
        interval = int(data.get("interval") or 5)
        expires_in = int(data.get("expires_in") or 900)
        if not user_code or not verification_url or not device_auth_id:
            raise ValueError("Codex device flow response missing required fields")

        session_id = str(uuid.uuid4())
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        self.db.execute(text("""
            INSERT INTO provider_auth_sessions
            (id, tenant_id, provider, flow_type, state, device_auth_id, user_code, verification_url, expires_at, status, created_by)
            VALUES (:id, :tenant_id, 'openai-codex', 'device_code', :state, :device_auth_id, :user_code, :verification_url, :expires_at, 'pending', :created_by)
        """), {
            "id": session_id,
            "tenant_id": tenant_id,
            "state": f"interval:{interval}",
            "device_auth_id": device_auth_id,
            "user_code": user_code,
            "verification_url": verification_url,
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
            SELECT id, device_auth_id, user_code, status, expires_at, created_by
            FROM provider_auth_sessions
            WHERE id = :id AND tenant_id = :tenant_id
        """), {"id": session_id, "tenant_id": tenant_id}).mappings().first()
        if not session:
            return {"status": "not_found"}
        if session["status"] != "pending":
            return {"status": session["status"]}

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self._url(self.device_token_path),
                json={"client_id": self.client_id, "device_code": session["device_auth_id"]},
                headers={"Accept": "application/json"},
            )
            if response.status_code in {400, 401}:
                data = response.json()
                error_code = data.get("error") or data.get("error_code") or "authorization_pending"
                if error_code in {"authorization_pending", "slow_down"}:
                    return {"status": "pending", "error_code": error_code}
                self._mark_session(session_id=session_id, tenant_id=tenant_id, status="failed", error_code=error_code)
                return {"status": "failed", "error_code": error_code}
            response.raise_for_status()
            data = response.json()

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
        if code_verifier:
            payload["code_verifier"] = code_verifier
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(self._url(self.token_path), data=payload, headers={"Accept": "application/json"})
            response.raise_for_status()
            return response.json()

    def _upsert_credential(self, *, tenant_id: str, created_by: str, access_token: str, refresh_token: str | None, expires_in: int, account_id: str | None, email: str | None, plan_type: str | None) -> str:
        credential_id = str(uuid.uuid4())
        profile_id = account_id or email or credential_id
        encrypted_access = self.crypto_service.encrypt(access_token)
        encrypted_refresh = self.crypto_service.encrypt(refresh_token) if refresh_token else None
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        fingerprint = self.crypto_service.get_safe_fingerprint("openai-codex", tenant_id, profile_id, access_token)
        row = self.db.execute(text("""
            INSERT INTO provider_credentials
            (id, tenant_id, provider, provider_runtime, credential_type, profile_id, account_id, email, chatgpt_plan_type,
             encrypted_access_token, encrypted_refresh_token, expires_at, status, token_fingerprint, created_by)
            VALUES
            (:id, :tenant_id, 'openai-codex', 'codex_app_server', 'oauth', :profile_id, :account_id, :email, :plan_type,
             :access, :refresh, :expires_at, 'active', :fingerprint, :created_by)
            ON CONFLICT (tenant_id, provider, profile_id) WHERE revoked_at IS NULL
            DO UPDATE SET encrypted_access_token = EXCLUDED.encrypted_access_token,
                          encrypted_refresh_token = EXCLUDED.encrypted_refresh_token,
                          expires_at = EXCLUDED.expires_at,
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
