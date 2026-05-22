from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..audit_service import log_admin_audit
from .codex_oauth_config import CODEX_PROVIDER, OPENCLAW_CODEX_CLIENT_ID, CodexOAuthConfig
from .credential_crypto import CredentialCryptoService
from .oauth_refresh_manager import OAuthRefreshManager


class CodexCredentialBroker:
    """Server-side Codex/Code X credential broker.

    This service intentionally never returns raw access or refresh tokens. It
    owns authorization-code start/callback, masked status, refresh, revoke, and
    disconnect operations for tenant-bound provider credentials.
    """

    def __init__(self, db: Session, crypto_service: CredentialCryptoService | None = None, config: CodexOAuthConfig | None = None):
        self.db = db
        self.crypto_service = crypto_service or CredentialCryptoService()
        self.config = config or CodexOAuthConfig.from_env()

    def start_authorization_code_flow(self, *, tenant_id: str, user_id: str, requested_scopes: list[str] | None = None) -> dict[str, Any]:
        self.config.require_authorization_code_flow()
        scope = self.config.normalize_scope(requested_scopes)
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(24)
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = _pkce_s256(code_verifier)
        session_id = str(uuid.uuid4())
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.config.state_ttl_seconds)

        self.db.execute(text("""
            INSERT INTO provider_auth_sessions
            (id, tenant_id, provider, flow_type, state, code_verifier, nonce, redirect_uri, scope,
             expires_at, status, created_by)
            VALUES
            (:id, :tenant_id, :provider, 'authorization_code', :state, :code_verifier, :nonce, :redirect_uri, :scope,
             :expires_at, 'pending', :created_by)
        """), {
            "id": session_id,
            "tenant_id": tenant_id,
            "provider": CODEX_PROVIDER,
            "state": state,
            "code_verifier": code_verifier,
            "nonce": nonce,
            "redirect_uri": self.config.redirect_uri,
            "scope": scope or None,
            "expires_at": expires_at,
            "created_by": user_id,
        })
        log_admin_audit(
            self.db,
            actor_id=_actor_id(user_id),
            action="codex_oauth_authorization_started",
            target_type="provider_auth_session",
            target_id=None,
            new_value={"session_id": session_id, "tenant_id": tenant_id, "provider": CODEX_PROVIDER, "scope": scope},
        )
        self.db.commit()

        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if scope:
            params["scope"] = scope
        if self.config.include_nonce:
            params["nonce"] = nonce
        if self.config.authorize_prompt:
            params["prompt"] = self.config.authorize_prompt

        separator = "&" if "?" in (self.config.authorization_url or "") else "?"
        authorization_url = f"{self.config.authorization_url}{separator}{urlencode(params)}"
        return {
            "session_id": session_id,
            "authorization_url": authorization_url,
            "expires_at": expires_at.isoformat(),
            "scope": scope,
            "provider": CODEX_PROVIDER,
        }

    def start_openclaw_manual_paste_flow(self, *, tenant_id: str, user_id: str) -> dict[str, Any]:
        scope = self.config.openclaw_manual_scope()
        redirect_uri = self.config.openclaw_manual_redirect_uri()
        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = _pkce_s256(code_verifier)
        session_id = str(uuid.uuid4())
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.config.state_ttl_seconds)

        self.db.execute(text("""
            INSERT INTO provider_auth_sessions
            (id, tenant_id, provider, flow_type, state, code_verifier, redirect_uri, scope,
             expires_at, status, created_by)
            VALUES
            (:id, :tenant_id, :provider, 'openclaw_manual_paste', :state, :code_verifier, :redirect_uri, :scope,
             :expires_at, 'pending', :created_by)
        """), {
            "id": session_id,
            "tenant_id": tenant_id,
            "provider": CODEX_PROVIDER,
            "state": state,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "expires_at": expires_at,
            "created_by": user_id,
        })
        self._audit(user_id, "codex_oauth_manual_started", {
            "session_id": session_id,
            "tenant_id": tenant_id,
            "scope": scope,
            "redirect_uri": redirect_uri,
        })
        self.db.commit()

        params = {
            "response_type": "code",
            "client_id": OPENCLAW_CODEX_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": "openclaw",
        }
        authorization_url = f"{self.config.openclaw_manual_authorization_url()}?{urlencode(params)}"
        return {
            "session_id": session_id,
            "authorization_url": authorization_url,
            "state": state,
            "expires_at": expires_at.isoformat(),
            "redirect_uri": redirect_uri,
            "scope": scope,
            "provider": CODEX_PROVIDER,
        }

    async def complete_openclaw_manual_paste_flow(
        self,
        *,
        tenant_id: str,
        session_id: str,
        authorization_response: str,
        actor_id: str,
    ) -> dict[str, Any]:
        started = time.monotonic()
        session = self._read_manual_session(tenant_id=tenant_id, session_id=session_id)
        if not session:
            raise ValueError("manual OAuth session not found")
        if session["status"] != "pending":
            raise ValueError("manual OAuth session is not pending")

        expires_at = _normalize_dt(session["expires_at"])
        if expires_at and expires_at < datetime.now(timezone.utc):
            self._mark_session(session_id=session_id, tenant_id=tenant_id, status="expired", error_code="expired")
            self._audit(actor_id, "codex_oauth_manual_failed", {
                "session_id": session_id,
                "tenant_id": tenant_id,
                "error_code": "expired",
            })
            self.db.commit()
            raise ValueError("manual OAuth session expired")

        parsed = _parse_authorization_response(authorization_response)
        code = parsed.get("code")
        response_state = parsed.get("state")
        expected_state = str(session["state"])
        if response_state and response_state != expected_state:
            self._mark_session(session_id=session_id, tenant_id=tenant_id, status="failed", error_code="state_mismatch")
            self._audit(actor_id, "codex_oauth_manual_failed", {
                "session_id": session_id,
                "tenant_id": tenant_id,
                "error_code": "state_mismatch",
            })
            self.db.commit()
            raise ValueError("manual OAuth state mismatch")
        if not code:
            self._mark_session(session_id=session_id, tenant_id=tenant_id, status="failed", error_code="missing_code")
            self._audit(actor_id, "codex_oauth_manual_failed", {
                "session_id": session_id,
                "tenant_id": tenant_id,
                "error_code": "missing_code",
            })
            self.db.commit()
            raise ValueError("manual OAuth response missing code")

        try:
            token_data = await self._exchange_authorization_code(
                code=code,
                code_verifier=str(session["code_verifier"]),
                redirect_uri=str(session["redirect_uri"]),
                token_url=self.config.openclaw_manual_token_url(),
                client_id=OPENCLAW_CODEX_CLIENT_ID,
                include_client_secret=False,
                require_refresh_token=True,
            )
            credential_id = self._upsert_credential(
                tenant_id=tenant_id,
                created_by=actor_id,
                token_data=token_data,
                session_scope=session["scope"],
            )
            self._mark_session(session_id=session_id, tenant_id=tenant_id, status="authorized", error_code=None)
            self._audit(actor_id, "codex_oauth_manual_completed", {
                "session_id": session_id,
                "credential_id": credential_id,
                "tenant_id": tenant_id,
                "scope": token_data.get("scope") or session["scope"],
            })
            self.db.commit()
            return {
                "ok": True,
                "status": "authorized",
                "credential_id": credential_id,
                "provider": CODEX_PROVIDER,
                "elapsed_ms": _elapsed_ms(started),
                "secret_values_exposed": False,
            }
        except Exception as exc:
            self.db.rollback()
            self._mark_session(session_id=session_id, tenant_id=tenant_id, status="failed", error_code="token_exchange_failed")
            self._audit(actor_id, "codex_oauth_manual_failed", {
                "session_id": session_id,
                "tenant_id": tenant_id,
                "error_code": "token_exchange_failed",
                "error_type": type(exc).__name__,
            })
            self.db.commit()
            raise ValueError("manual OAuth token exchange failed") from exc

    async def complete_authorization_code_callback(
        self,
        *,
        state: str | None,
        code: str | None,
        error: str | None,
        error_description: str | None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        self.config.require_authorization_code_flow()
        if not state:
            return {"ok": False, "status": "failed", "error_code": "missing_state", "elapsed_ms": _elapsed_ms(started)}

        session = self._read_pending_session_by_state(state)
        if not session:
            return {"ok": False, "status": "failed", "error_code": "invalid_or_replayed_state", "elapsed_ms": _elapsed_ms(started)}

        if session["status"] != "pending":
            return {"ok": False, "status": "failed", "error_code": "invalid_or_replayed_state", "elapsed_ms": _elapsed_ms(started)}

        session_id = str(session["id"])
        tenant_id = str(session["tenant_id"])
        created_by = str(session["created_by"] or "")
        expires_at = _normalize_dt(session["expires_at"])
        if expires_at and expires_at < datetime.now(timezone.utc):
            self._mark_session(session_id=session_id, tenant_id=tenant_id, status="expired", error_code="expired")
            self._audit(created_by, "codex_oauth_callback_expired", {"session_id": session_id, "tenant_id": tenant_id})
            self.db.commit()
            return {"ok": False, "status": "expired", "error_code": "expired", "elapsed_ms": _elapsed_ms(started)}

        if error:
            safe_error = _safe_error_code(error)
            self._mark_session(session_id=session_id, tenant_id=tenant_id, status="failed", error_code=safe_error)
            self._audit(created_by, "codex_oauth_callback_provider_error", {
                "session_id": session_id,
                "tenant_id": tenant_id,
                "error_code": safe_error,
                "has_error_description": bool(error_description),
            })
            self.db.commit()
            return {"ok": False, "status": "failed", "error_code": safe_error, "elapsed_ms": _elapsed_ms(started)}

        if not code:
            self._mark_session(session_id=session_id, tenant_id=tenant_id, status="failed", error_code="missing_code")
            self._audit(created_by, "codex_oauth_callback_missing_code", {"session_id": session_id, "tenant_id": tenant_id})
            self.db.commit()
            return {"ok": False, "status": "failed", "error_code": "missing_code", "elapsed_ms": _elapsed_ms(started)}

        try:
            token_data = await self._exchange_authorization_code(
                code=code,
                code_verifier=str(session["code_verifier"]),
                redirect_uri=str(session["redirect_uri"] or self.config.redirect_uri),
            )
            credential_id = self._upsert_credential(
                tenant_id=tenant_id,
                created_by=created_by,
                token_data=token_data,
                session_scope=session["scope"],
            )
            self._mark_session(session_id=session_id, tenant_id=tenant_id, status="authorized", error_code=None)
            self._audit(created_by, "codex_oauth_token_exchange_succeeded", {
                "session_id": session_id,
                "credential_id": credential_id,
                "tenant_id": tenant_id,
                "scope": token_data.get("scope") or session["scope"],
            })
            self.db.commit()
            return {
                "ok": True,
                "status": "authorized",
                "credential_id": credential_id,
                "provider": CODEX_PROVIDER,
                "elapsed_ms": _elapsed_ms(started),
            }
        except Exception as exc:
            self.db.rollback()
            self._mark_session(session_id=session_id, tenant_id=tenant_id, status="failed", error_code="token_exchange_failed")
            self._audit(created_by, "codex_oauth_token_exchange_failed", {
                "session_id": session_id,
                "tenant_id": tenant_id,
                "error_code": "token_exchange_failed",
                "error_type": type(exc).__name__,
            })
            self.db.commit()
            return {"ok": False, "status": "failed", "error_code": "token_exchange_failed", "elapsed_ms": _elapsed_ms(started)}

    def list_credentials(self, *, tenant_id: str) -> dict[str, Any]:
        rows = self.db.execute(text("""
            SELECT id, tenant_id, provider, provider_runtime, credential_type, profile_id, account_id, email,
                   chatgpt_plan_type, expires_at, status, last_used_at, last_refresh_at, last_error_code,
                   token_fingerprint, created_by, created_at, updated_at, revoked_at, scope
            FROM provider_credentials
            WHERE tenant_id = :tenant_id AND provider = :provider
            ORDER BY created_at DESC
        """), {"tenant_id": tenant_id, "provider": CODEX_PROVIDER}).mappings().all()
        credentials = [_mask_credential(row) for row in rows]
        return {
            "provider": CODEX_PROVIDER,
            "tenant_id": tenant_id,
            "credentials": credentials,
            "active_count": sum(1 for row in credentials if row["status"] == "active" and not row.get("revoked_at")),
            "secret_values_exposed": False,
        }

    def get_session_status(self, *, tenant_id: str, session_id: str) -> dict[str, Any]:
        row = self.db.execute(text("""
            SELECT id, tenant_id, provider, flow_type, status, expires_at, error_code, user_code,
                   verification_url, scope, created_at, completed_at
            FROM provider_auth_sessions
            WHERE tenant_id = :tenant_id AND id = :id AND provider = :provider
        """), {"tenant_id": tenant_id, "id": session_id, "provider": CODEX_PROVIDER}).mappings().first()
        if not row:
            return {"status": "not_found"}
        expires_at = _normalize_dt(row["expires_at"])
        if row["status"] == "pending" and expires_at and expires_at < datetime.now(timezone.utc):
            self._mark_session(session_id=session_id, tenant_id=tenant_id, status="expired", error_code="expired")
            self.db.commit()
            return {"status": "expired", "error_code": "expired"}
        return {
            "session_id": row["id"],
            "provider": row["provider"],
            "flow_type": row["flow_type"],
            "status": row["status"],
            "error_code": row["error_code"],
            "expires_at": _iso(row["expires_at"]),
            "completed_at": _iso(row["completed_at"]),
            "scope": row["scope"],
            "user_code": row["user_code"] if row["status"] == "pending" else None,
            "verification_url": row["verification_url"] if row["status"] == "pending" else None,
        }

    async def refresh_credential(self, *, tenant_id: str, credential_id: str, actor_id: int | None) -> dict[str, Any]:
        manager = OAuthRefreshManager(self.db, self.crypto_service)
        token = await manager.get_valid_access_token(tenant_id, credential_id)
        if not token:
            self._audit(str(actor_id or ""), "codex_oauth_refresh_failed", {"credential_id": credential_id, "tenant_id": tenant_id})
            self.db.commit()
            return {"ok": False, "status": "failed", "error_code": "refresh_failed"}
        self._audit(str(actor_id or ""), "codex_oauth_refresh_succeeded", {"credential_id": credential_id, "tenant_id": tenant_id})
        self.db.commit()
        return {"ok": True, "status": "active", "credential_id": credential_id}

    async def revoke_credential(self, *, tenant_id: str, credential_id: str, actor_id: int | None) -> dict[str, Any]:
        row = self._read_credential_for_revoke(tenant_id=tenant_id, credential_id=credential_id)
        if not row:
            return {"ok": False, "status": "not_found", "error_code": "credential_not_found"}
        upstream_status = "not_configured"
        error_code = None
        if self.config.revoke_url:
            token = self.crypto_service.decrypt(row["encrypted_refresh_token"] or row["encrypted_access_token"])
            upstream_status = await self._perform_upstream_revoke(token)
            if upstream_status != "ok":
                error_code = "upstream_revoke_failed"
        self._mark_credential_revoked(tenant_id=tenant_id, credential_id=credential_id, error_code=error_code)
        self._audit(str(actor_id or ""), "codex_oauth_credential_revoked", {
            "credential_id": credential_id,
            "tenant_id": tenant_id,
            "upstream_revoke": upstream_status,
            "error_code": error_code,
        })
        self.db.commit()
        return {"ok": True, "status": "revoked", "credential_id": credential_id, "upstream_revoke": upstream_status, "error_code": error_code}

    def disconnect_credential(self, *, tenant_id: str, credential_id: str, actor_id: int | None) -> dict[str, Any]:
        row = self._read_credential_for_revoke(tenant_id=tenant_id, credential_id=credential_id)
        if not row:
            return {"ok": False, "status": "not_found", "error_code": "credential_not_found"}
        self._mark_credential_revoked(tenant_id=tenant_id, credential_id=credential_id, error_code=None)
        self._audit(str(actor_id or ""), "codex_oauth_credential_disconnected", {"credential_id": credential_id, "tenant_id": tenant_id})
        self.db.commit()
        return {"ok": True, "status": "revoked", "credential_id": credential_id}

    def _read_pending_session_by_state(self, state: str):
        return self.db.execute(text("""
            SELECT id, tenant_id, provider, flow_type, state, code_verifier, nonce, redirect_uri, scope,
                   expires_at, status, error_code, created_by
            FROM provider_auth_sessions
            WHERE provider = :provider AND state = :state AND flow_type = 'authorization_code'
            ORDER BY created_at DESC
            LIMIT 1
        """), {"provider": CODEX_PROVIDER, "state": state}).mappings().first()

    def _read_manual_session(self, *, tenant_id: str, session_id: str):
        return self.db.execute(text("""
            SELECT id, tenant_id, provider, flow_type, state, code_verifier, redirect_uri, scope,
                   expires_at, status, error_code, created_by
            FROM provider_auth_sessions
            WHERE tenant_id = :tenant_id AND id = :id AND provider = :provider
              AND flow_type = 'openclaw_manual_paste'
            LIMIT 1
        """), {"tenant_id": tenant_id, "id": session_id, "provider": CODEX_PROVIDER}).mappings().first()

    async def _exchange_authorization_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        token_url: str | None = None,
        client_id: str | None = None,
        include_client_secret: bool = True,
        require_refresh_token: bool = False,
    ) -> dict[str, Any]:
        if token_url is None or client_id is None:
            self.config.require_token_endpoint()
        resolved_token_url = token_url or self.config.token_url
        resolved_client_id = client_id or self.config.client_id
        if not resolved_token_url or not resolved_client_id:
            raise ValueError("Codex token endpoint missing configuration")
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": resolved_client_id,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }
        if include_client_secret and self.config.client_secret:
            payload["client_secret"] = self.config.client_secret
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.post(resolved_token_url, data=payload, headers={"Accept": "application/json"})
            response.raise_for_status()
            data = response.json()
        if not (data.get("access_token") or data.get("access")):
            raise ValueError("Codex token response missing access token")
        if require_refresh_token and not (data.get("refresh_token") or data.get("refresh")):
            raise ValueError("Codex token response missing refresh token")
        return data

    def _upsert_credential(self, *, tenant_id: str, created_by: str, token_data: dict[str, Any], session_scope: str | None) -> str:
        access_token = token_data.get("access_token") or token_data.get("access")
        refresh_token = token_data.get("refresh_token") or token_data.get("refresh")
        expires_in = int(token_data.get("expires_in") or token_data.get("expires") or 3600)
        if not access_token:
            raise ValueError("Codex token response missing access token")
        identity = _extract_codex_identity(access_token)
        account_id = token_data.get("account_id") or token_data.get("accountId") or identity.get("account_id")
        email = token_data.get("email") or identity.get("email")
        plan_type = token_data.get("chatgpt_plan_type") or token_data.get("chatgptPlanType") or identity.get("chatgpt_plan_type")
        profile_id = account_id or email or self.crypto_service.get_safe_fingerprint(CODEX_PROVIDER, tenant_id, "profile", access_token) or str(uuid.uuid4())
        credential_id = str(uuid.uuid4())
        encrypted_access = self.crypto_service.encrypt(access_token)
        encrypted_refresh = self.crypto_service.encrypt(refresh_token) if refresh_token else None
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=expires_in)
        fingerprint = self.crypto_service.get_safe_fingerprint(CODEX_PROVIDER, tenant_id, profile_id, access_token)
        scope = token_data.get("scope") or session_scope
        row = self.db.execute(text("""
            INSERT INTO provider_credentials
            (id, tenant_id, provider, provider_runtime, credential_type, profile_id, account_id, email, chatgpt_plan_type,
             encrypted_access_token, encrypted_refresh_token, expires_at, status, token_fingerprint, created_by, scope)
            VALUES
            (:id, :tenant_id, :provider, 'codex_app_server', 'oauth', :profile_id, :account_id, :email, :plan_type,
             :access, :refresh, :expires_at, 'active', :fingerprint, :created_by, :scope)
            ON CONFLICT (tenant_id, provider, profile_id) WHERE revoked_at IS NULL
            DO UPDATE SET encrypted_access_token = EXCLUDED.encrypted_access_token,
                          encrypted_refresh_token = EXCLUDED.encrypted_refresh_token,
                          expires_at = EXCLUDED.expires_at,
                          scope = EXCLUDED.scope,
                          status = 'active',
                          last_error_code = NULL,
                          updated_at = :updated_at
            RETURNING id
        """), {
            "id": credential_id,
            "tenant_id": tenant_id,
            "provider": CODEX_PROVIDER,
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
            "updated_at": now,
        }).first()
        return str(row[0]) if row else credential_id

    def _read_credential_for_revoke(self, *, tenant_id: str, credential_id: str):
        return self.db.execute(text("""
            SELECT id, encrypted_access_token, encrypted_refresh_token, revoked_at
            FROM provider_credentials
            WHERE tenant_id = :tenant_id AND id = :id AND provider = :provider
        """), {"tenant_id": tenant_id, "id": credential_id, "provider": CODEX_PROVIDER}).mappings().first()

    async def _perform_upstream_revoke(self, token: str | None) -> str:
        if not token or not self.config.revoke_url:
            return "not_configured"
        payload = {"token": token, "client_id": self.config.client_id}
        if self.config.client_secret:
            payload["client_secret"] = self.config.client_secret
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
                response = await client.post(self.config.revoke_url, data=payload, headers={"Accept": "application/json"})
            if response.status_code < 400:
                return "ok"
            return "failed"
        except httpx.HTTPError:
            return "failed"

    def _mark_session(self, *, session_id: str, tenant_id: str, status: str, error_code: str | None) -> None:
        completed = datetime.now(timezone.utc) if status in {"authorized", "failed", "expired", "cancelled"} else None
        self.db.execute(text("""
            UPDATE provider_auth_sessions
            SET status = :status, error_code = :error_code, completed_at = :completed
            WHERE id = :id AND tenant_id = :tenant_id AND provider = :provider
        """), {"status": status, "error_code": error_code, "completed": completed, "id": session_id, "tenant_id": tenant_id, "provider": CODEX_PROVIDER})

    def _mark_credential_revoked(self, *, tenant_id: str, credential_id: str, error_code: str | None) -> None:
        now = datetime.now(timezone.utc)
        self.db.execute(text("""
            UPDATE provider_credentials
            SET status = 'revoked', revoked_at = COALESCE(revoked_at, :now), last_error_code = :error_code, updated_at = :now
            WHERE tenant_id = :tenant_id AND id = :id AND provider = :provider
        """), {"tenant_id": tenant_id, "id": credential_id, "provider": CODEX_PROVIDER, "now": now, "error_code": error_code})

    def _audit(self, created_by: str, action: str, value: dict[str, Any]) -> None:
        log_admin_audit(
            self.db,
            actor_id=_actor_id(created_by),
            action=action,
            target_type="provider_credential",
            target_id=None,
            new_value=value,
        )


def _pkce_s256(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _parse_authorization_response(value: str) -> dict[str, str]:
    trimmed = (value or "").strip()
    if not trimmed:
        return {}
    if "://" in trimmed:
        parsed = urlparse(trimmed)
        params = parse_qs(parsed.query, keep_blank_values=False)
        return {key: values[-1] for key, values in params.items() if values}
    if "code=" in trimmed or "state=" in trimmed:
        params = parse_qs(trimmed.lstrip("?"), keep_blank_values=False)
        return {key: values[-1] for key, values in params.items() if values}
    return {"code": trimmed}


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode((payload + padding).encode("ascii")).decode("utf-8")
        data = json.loads(decoded)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _extract_codex_identity(access_token: str) -> dict[str, str]:
    payload = _decode_jwt_payload(access_token)
    auth_claim = payload.get("https://api.openai.com/auth")
    profile_claim = payload.get("https://api.openai.com/profile")
    auth = auth_claim if isinstance(auth_claim, dict) else {}
    profile = profile_claim if isinstance(profile_claim, dict) else {}
    identity: dict[str, str] = {}
    account_id = auth.get("chatgpt_account_id")
    if isinstance(account_id, str) and account_id.strip():
        identity["account_id"] = account_id.strip()
    plan_type = auth.get("chatgpt_plan_type")
    if isinstance(plan_type, str) and plan_type.strip():
        identity["chatgpt_plan_type"] = plan_type.strip()
    email = profile.get("email")
    if isinstance(email, str) and email.strip():
        identity["email"] = email.strip()
    return identity


def _actor_id(value: str | int | None) -> int | None:
    try:
        return int(value) if value is not None and str(value).strip() else None
    except (TypeError, ValueError):
        return None


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _safe_error_code(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in value.strip())
    return cleaned[:120] or "provider_error"


def _normalize_dt(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _iso(value) -> str | None:
    normalized = _normalize_dt(value)
    return normalized.isoformat() if normalized else None


def _mask_credential(row) -> dict[str, Any]:
    fingerprint = row["token_fingerprint"] or ""
    return {
        "id": row["id"],
        "tenant_id": row["tenant_id"],
        "provider": row["provider"],
        "provider_runtime": row["provider_runtime"],
        "credential_type": row["credential_type"],
        "profile_id": row["profile_id"],
        "account_id": row["account_id"],
        "email": row["email"],
        "chatgpt_plan_type": row["chatgpt_plan_type"],
        "scope": row["scope"],
        "expires_at": _iso(row["expires_at"]),
        "status": row["status"],
        "last_used_at": _iso(row["last_used_at"]),
        "last_refresh_at": _iso(row["last_refresh_at"]),
        "last_error_code": row["last_error_code"],
        "token_fingerprint_prefix": fingerprint[:12] if fingerprint else None,
        "created_by": row["created_by"],
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
        "revoked_at": _iso(row["revoked_at"]),
        "secret_values_exposed": False,
    }
