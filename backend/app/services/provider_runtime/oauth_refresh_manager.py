from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urljoin

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class OAuthRefreshManager:
    _locks: dict[str, asyncio.Lock] = {}
    _locks_lock = asyncio.Lock()

    def __init__(self, db: Session, crypto_service):
        self.db = db
        self.crypto_service = crypto_service

    async def _get_lock(self, key: str) -> asyncio.Lock:
        async with self._locks_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    async def get_valid_access_token(self, tenant_id: str, credential_id: str) -> Optional[str]:
        result = self._read_credential(tenant_id=tenant_id, credential_id=credential_id, include_refresh=True)
        if not result or result["status"] in {"revoked", "error", "pending"}:
            return None

        expires_at = self._normalize_dt(result["expires_at"])
        if expires_at is None:
            return self.crypto_service.decrypt(result["encrypted_access_token"])

        now = datetime.now(timezone.utc)
        if expires_at > now + timedelta(minutes=5):
            return self.crypto_service.decrypt(result["encrypted_access_token"])

        return await self._refresh_with_lock(
            tenant_id=tenant_id,
            credential_id=credential_id,
            provider=result["provider"],
            encrypted_refresh_token=result["encrypted_refresh_token"],
        )

    def _read_credential(self, *, tenant_id: str, credential_id: str, include_refresh: bool):
        refresh_col = ", encrypted_refresh_token" if include_refresh else ""
        query = text(f"""
            SELECT provider, status, expires_at, encrypted_access_token{refresh_col}
            FROM provider_credentials
            WHERE tenant_id = :tenant_id AND id = :credential_id AND revoked_at IS NULL
        """)
        return self.db.execute(query, {"tenant_id": tenant_id, "credential_id": credential_id}).mappings().first()

    @staticmethod
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

    @staticmethod
    def _stable_pg_lock_id(lock_key: str) -> int:
        # PostgreSQL advisory locks accept signed bigint. Keep the top bit clear.
        return int(hashlib.sha256(lock_key.encode("utf-8")).hexdigest()[:15], 16)

    def _is_postgres(self) -> bool:
        bind = self.db.get_bind()
        dialect = getattr(bind, "dialect", None)
        return "postgres" in str(getattr(dialect, "name", "")).lower()

    def _obtain_pg_lock(self, lock_key: str) -> bool:
        if not self._is_postgres():
            return True
        lock_id = self._stable_pg_lock_id(lock_key)
        try:
            self.db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})
            return True
        except Exception as exc:
            logger.error("provider_oauth_refresh_lock_failed", extra={"lock_key_hash": hashlib.sha256(lock_key.encode()).hexdigest(), "error": str(exc)})
            self.db.rollback()
            return False

    async def _refresh_with_lock(self, *, tenant_id: str, credential_id: str, provider: str, encrypted_refresh_token: str) -> Optional[str]:
        lock_key = f"oauth-refresh:{tenant_id}:{provider}:{credential_id}"
        process_lock = await self._get_lock(lock_key)

        async with process_lock:
            if not self._obtain_pg_lock(lock_key):
                return None

            refreshed = self._read_credential(tenant_id=tenant_id, credential_id=credential_id, include_refresh=False)
            if not refreshed or refreshed["status"] in {"revoked", "error", "pending"}:
                return None

            expires_at = self._normalize_dt(refreshed["expires_at"])
            now = datetime.now(timezone.utc)
            if expires_at and expires_at > now + timedelta(minutes=5):
                return self.crypto_service.decrypt(refreshed["encrypted_access_token"])

            refresh_token = self.crypto_service.decrypt(encrypted_refresh_token)
            if not refresh_token:
                self._mark_refresh_failed(tenant_id=tenant_id, credential_id=credential_id, now=now)
                return None

            new_access_token, new_refresh_token, new_expires_in = await self._perform_http_refresh(provider, refresh_token)
            if not new_access_token:
                self._mark_refresh_failed(tenant_id=tenant_id, credential_id=credential_id, now=now)
                return None

            new_expires_at = now + timedelta(seconds=int(new_expires_in or 3600))
            enc_access = self.crypto_service.encrypt(new_access_token)
            enc_refresh = self.crypto_service.encrypt(new_refresh_token) if new_refresh_token else encrypted_refresh_token
            self.db.execute(text("""
                UPDATE provider_credentials
                SET encrypted_access_token = :access,
                    encrypted_refresh_token = :refresh,
                    expires_at = :expires_at,
                    last_refresh_at = :now,
                    last_error_code = NULL,
                    updated_at = :now
                WHERE tenant_id = :tenant_id AND id = :id AND revoked_at IS NULL
            """), {
                "tenant_id": tenant_id,
                "id": credential_id,
                "access": enc_access,
                "refresh": enc_refresh,
                "expires_at": new_expires_at,
                "now": now,
            })
            self.db.commit()
            return new_access_token

    def _mark_refresh_failed(self, *, tenant_id: str, credential_id: str, now: datetime) -> None:
        self.db.execute(text("""
            UPDATE provider_credentials
            SET last_error_code = 'refresh_failed', updated_at = :now
            WHERE tenant_id = :tenant_id AND id = :id AND revoked_at IS NULL
        """), {"tenant_id": tenant_id, "id": credential_id, "now": now})
        self.db.commit()

    async def _perform_http_refresh(self, provider: str, refresh_token: str) -> Tuple[Optional[str], Optional[str], int]:
        if provider != "openai-codex":
            return None, None, 0

        token_url = _codex_token_url()
        client_id = os.environ.get("CODEX_OAUTH_CLIENT_ID", "").strip()
        if not client_id or not token_url:
            logger.error("codex_oauth_refresh_disabled_or_misconfigured")
            return None, None, 0

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }
        client_secret = _codex_client_secret()
        if client_secret:
            payload["client_secret"] = client_secret
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
                response = await client.post(token_url, data=payload, headers={"Accept": "application/json"})
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            logger.error("codex_oauth_http_refresh_failed", extra={"error": str(exc)})
            return None, None, 0
        except Exception as exc:
            logger.error("codex_oauth_refresh_failed", extra={"error": str(exc)})
            return None, None, 0

        access_token = data.get("access_token") or data.get("access")
        new_refresh = data.get("refresh_token") or data.get("refresh")
        expires_in = int(data.get("expires_in") or data.get("expires") or 3600)
        if not access_token:
            return None, None, 0
        return access_token, new_refresh, expires_in


def _codex_token_url() -> str | None:
    raw = (os.environ.get("CODEX_OAUTH_TOKEN_URL") or os.environ.get("CODEX_OAUTH_TOKEN_PATH") or "").strip()
    if not raw:
        return None
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    base = os.environ.get("CODEX_OAUTH_AUTH_BASE_URL", "").strip().rstrip("/")
    if not base:
        return raw
    return urljoin(base + "/", raw.lstrip("/"))


def _codex_client_secret() -> str | None:
    file_path = os.environ.get("CODEX_OAUTH_CLIENT_SECRET_FILE", "").strip()
    if file_path:
        try:
            return Path(file_path).read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
    raw = os.environ.get("CODEX_OAUTH_CLIENT_SECRET", "").strip()
    if raw and os.environ.get("APP_ENV", "development").strip().lower() != "production":
        return raw
    return None
