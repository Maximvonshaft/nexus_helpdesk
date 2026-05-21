import json
import uuid
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Dict, Any

class CodexAuthProfileImporter:
    def __init__(self, db: Session, crypto_service):
        self.db = db
        self.crypto_service = crypto_service

    def import_profile(self, tenant_id: str, profile_data: Dict[str, Any], created_by: str) -> str:
        # Expected OpenClaw format
        # {"type": "oauth", "provider": "openai-codex", "access": "...", "refresh": "...", "expires": "ISO8601", "accountId": "...", "chatgptPlanType": "..."}
        
        provider = profile_data.get("provider", "openai-codex")
        cred_type = profile_data.get("type", "oauth")
        access = profile_data.get("access")
        refresh = profile_data.get("refresh")
        expires_str = profile_data.get("expires")
        
        if not access:
            raise ValueError("Missing access token in profile")
            
        profile_id = profile_data.get("accountId") or str(uuid.uuid4())
        
        expires_at = None
        if expires_str:
            try:
                expires_at = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
            except ValueError:
                pass
                
        enc_access = self.crypto_service.encrypt(access)
        enc_refresh = self.crypto_service.encrypt(refresh) if refresh else None
        
        fingerprint = self.crypto_service.get_safe_fingerprint(provider, tenant_id, profile_id, access)
        
        cred_id = str(uuid.uuid4())
        
        self.db.execute(
            text("""
                INSERT INTO provider_credentials
                (id, tenant_id, provider, provider_runtime, credential_type, profile_id, account_id, email, chatgpt_plan_type, 
                encrypted_access_token, encrypted_refresh_token, expires_at, status, token_fingerprint, created_by)
                VALUES
                (:id, :tenant_id, :provider, :runtime, :cred_type, :profile_id, :account_id, :email, :plan_type,
                :enc_access, :enc_refresh, :expires_at, 'active', :fingerprint, :created_by)
                ON CONFLICT (tenant_id, provider, profile_id) WHERE revoked_at IS NULL
                DO UPDATE SET 
                    encrypted_access_token = EXCLUDED.encrypted_access_token,
                    encrypted_refresh_token = EXCLUDED.encrypted_refresh_token,
                    expires_at = EXCLUDED.expires_at,
                    status = 'active',
                    updated_at = now()
                RETURNING id
            """),
            {
                "id": cred_id,
                "tenant_id": tenant_id,
                "provider": provider,
                "runtime": cred_type,
                "cred_type": cred_type,
                "profile_id": profile_id,
                "account_id": profile_data.get("accountId"),
                "email": profile_data.get("email"),
                "plan_type": profile_data.get("chatgptPlanType"),
                "enc_access": enc_access,
                "enc_refresh": enc_refresh,
                "expires_at": expires_at,
                "fingerprint": fingerprint,
                "created_by": created_by
            }
        )
        self.db.commit()
        return cred_id
