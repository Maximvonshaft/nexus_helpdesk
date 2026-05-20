import os
from datetime import datetime, timezone, timedelta
import httpx
from sqlalchemy.orm import Session
from sqlalchemy import text

class CodexDeviceAuthService:
    def __init__(self, db: Session, crypto_service):
        self.db = db
        self.crypto_service = crypto_service
        self.enabled = os.environ.get("CODEX_OAUTH_DEVICE_FLOW_ENABLED", "false").lower() == "true"
        self.auth_base_url = os.environ.get("CODEX_OAUTH_AUTH_BASE_URL", "https://auth.openai.com")
        self.client_id = os.environ.get("CODEX_OAUTH_CLIENT_ID", "")
        
    async def start_device_flow(self, tenant_id: str, user_id: str) -> dict:
        if not self.enabled:
            raise ValueError("Device flow is disabled")
            
        # In a real implementation we would hit self.auth_base_url + /oauth/device/code
        # For this test double we just mock the payload
        verification_url = f"{self.auth_base_url}/device"
        user_code = "ABCD-EFGH"
        device_auth_id = "test_device_id"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
        
        session_id = "test_sess_" + user_code
        
        self.db.execute(
            text("""
                INSERT INTO provider_auth_sessions 
                (id, tenant_id, provider, flow_type, state, device_auth_id, user_code, verification_url, expires_at, status, created_by)
                VALUES (:id, :tenant_id, 'openai-codex', 'device_code', 'pending', :device_auth_id, :user_code, :verification_url, :expires_at, 'pending', :created_by)
            """),
            {
                "id": session_id,
                "tenant_id": tenant_id,
                "device_auth_id": device_auth_id,
                "user_code": user_code,
                "verification_url": verification_url,
                "expires_at": expires_at,
                "created_by": user_id
            }
        )
        self.db.commit()
        
        return {
            "session_id": session_id,
            "verification_url": verification_url,
            "user_code": user_code,
            "expires_at": expires_at.isoformat()
        }

    async def status_device_flow(self, tenant_id: str, session_id: str) -> dict:
        result = self.db.execute(
            text("SELECT status FROM provider_auth_sessions WHERE id = :id AND tenant_id = :tenant_id"),
            {"id": session_id, "tenant_id": tenant_id}
        ).mappings().first()
        
        if not result:
            return {"status": "not_found"}
            
        return {"status": result['status']}
        
    async def poll_device_flow(self, tenant_id: str, session_id: str) -> dict:
        # Mock poll logic that would transition pending -> authorized
        # Not returning tokens here per requirements
        return {"status": "pending"}
