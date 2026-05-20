import asyncio
from datetime import datetime, timezone, timedelta
import httpx
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional, Tuple
import logging
import hashlib
import os

logger = logging.getLogger(__name__)

class OAuthRefreshManager:
    _locks = {}
    _locks_lock = asyncio.Lock()

    def __init__(self, db: Session, crypto_service):
        self.db = db
        self.crypto_service = crypto_service

    async def _get_lock(self, key: str):
        async with self._locks_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    async def get_valid_access_token(self, tenant_id: str, credential_id: str) -> Optional[str]:
        def _read_token():
            query = text("""
                SELECT provider, status, expires_at, encrypted_access_token, encrypted_refresh_token
                FROM provider_credentials 
                WHERE tenant_id = :tenant_id AND id = :credential_id AND revoked_at IS NULL
            """)
            return self.db.execute(query, {"tenant_id": tenant_id, "credential_id": credential_id}).mappings().first()
            
        result = await asyncio.to_thread(_read_token)
        if not result or result['status'] in ('revoked', 'error', 'pending'): return None
            
        expires_at = result['expires_at']
        if not expires_at:
            return self.crypto_service.decrypt(result['encrypted_access_token'])
            
        now = datetime.now(timezone.utc)
        if expires_at.tzinfo is None: expires_at = expires_at.replace(tzinfo=timezone.utc)
            
        if expires_at > now + timedelta(minutes=5):
            return self.crypto_service.decrypt(result['encrypted_access_token'])
            
        return await self._refresh_with_lock(tenant_id, credential_id, result['provider'], result['encrypted_refresh_token'])

    async def _refresh_with_lock(self, tenant_id: str, credential_id: str, provider: str, encrypted_refresh_token: str) -> Optional[str]:
        lock_key_str = f"oauth-refresh:{tenant_id}:{provider}:{credential_id}"
        process_lock = await self._get_lock(lock_key_str)
        
        async with process_lock:
            def _obtain_pg_lock_and_read():
                # Stable 64-bit int from sha256 for pg_advisory_xact_lock
                hash_hex = hashlib.sha256(lock_key_str.encode('utf-8')).hexdigest()
                lock_id = int(hash_hex[:15], 16)
                try:
                    self.db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})
                except Exception as e:
                    # Check dialect to safely ignore if SQLite, but fail closed on Postgres
                    bind = self.db.get_bind()
                    dialect = getattr(bind, "dialect", None)
                    dialect_name = str(getattr(dialect, "name", "")).lower()
                    if "postgres" in dialect_name:
                        logger.error(f"Failed to obtain pg_advisory_xact_lock for {lock_key_str}: {e}")
                        raise RuntimeError("Refresh lock acquisition failed")
                
                query = text("""
                    SELECT status, expires_at, encrypted_access_token
                    FROM provider_credentials 
                    WHERE id = :credential_id
                """)
                return self.db.execute(query, {"credential_id": credential_id}).mappings().first()

            try:
                result = await asyncio.to_thread(_obtain_pg_lock_and_read)
            except RuntimeError:
                return None
                
            now = datetime.now(timezone.utc)
            expires_at = result['expires_at']
            if expires_at and expires_at.tzinfo is None: expires_at = expires_at.replace(tzinfo=timezone.utc)
                
            if expires_at and expires_at > now + timedelta(minutes=5):
                return self.crypto_service.decrypt(result['encrypted_access_token'])
                
            refresh_token = self.crypto_service.decrypt(encrypted_refresh_token)
            if not refresh_token: return None
                
            new_access_token, new_refresh_token, new_expires_in = await self._perform_http_refresh(provider, refresh_token)
            
            def _write_result():
                if not new_access_token:
                    self.db.execute(text("UPDATE provider_credentials SET last_error_code = 'refresh_failed', updated_at = :now WHERE id = :id"), {"id": credential_id, "now": now})
                    self.db.commit()
                    return None
                    
                new_expires_at = now + timedelta(seconds=new_expires_in)
                enc_access = self.crypto_service.encrypt(new_access_token)
                enc_refresh = self.crypto_service.encrypt(new_refresh_token) if new_refresh_token else encrypted_refresh_token
                
                self.db.execute(text("""
                    UPDATE provider_credentials 
                    SET encrypted_access_token = :access, encrypted_refresh_token = :refresh,
                        expires_at = :expires_at, last_refresh_at = :now, last_error_code = NULL, updated_at = :now
                    WHERE id = :id
                """), {"access": enc_access, "refresh": enc_refresh, "expires_at": new_expires_at, "now": now, "id": credential_id})
                self.db.commit()
                return new_access_token
                
            return await asyncio.to_thread(_write_result)
        
    async def _perform_http_refresh(self, provider: str, refresh_token: str) -> Tuple[Optional[str], Optional[str], int]:
        if provider == "openai-codex":
            token_url = os.environ.get("CODEX_OAUTH_TOKEN_PATH", "https://auth.openai.com/oauth/token")
            client_id = os.environ.get("CODEX_OAUTH_CLIENT_ID", "")
            if not client_id or not token_url:
                logger.error("Codex OAuth refresh disabled or missing client_id/token_url")
                return None, None, 0
                
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        token_url,
                        json={
                            "grant_type": "refresh_token",
                            "refresh_token": refresh_token,
                            "client_id": client_id
                        }
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    
                    access_token = data.get("access_token")
                    new_refresh = data.get("refresh_token")
                    expires_in = data.get("expires_in", 3600)
                    
                    if not access_token:
                        return None, None, 0
                    
                    return access_token, new_refresh, expires_in
            except httpx.HTTPError as e:
                logger.error(f"Codex OAuth HTTP refresh failed: {e}")
                return None, None, 0
            except Exception as e:
                logger.error(f"Codex OAuth generic refresh error: {e}")
                return None, None, 0
                
        return None, None, 0
