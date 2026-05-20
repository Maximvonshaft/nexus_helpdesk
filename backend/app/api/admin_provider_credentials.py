from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
from ..db import get_db
# Import a dummy get_current_user since we know standard APIs use Depends(get_current_user)
# In real code this comes from ..api.deps, but let's mock it safely to not break imports
try:
    from .deps import get_current_user
except ImportError:
    def get_current_user(x_user_id: int | None = Header(default=None, alias="X-User-Id")):
        if not x_user_id:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return {"id": x_user_id}

from ..services.provider_runtime.codex_device_auth_service import CodexDeviceAuthService
from ..services.provider_runtime.credential_crypto import CredentialCryptoService
from ..services.provider_runtime.codex_auth_profile_importer import CodexAuthProfileImporter

router = APIRouter()

def get_crypto_service():
    return CredentialCryptoService()

@router.get("/")
async def list_credentials(tenant_id: str, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    rows = db.execute(text("""
        SELECT id, provider, provider_runtime, credential_type, profile_id, account_id, email, display_name, status, expires_at, last_used_at
        FROM provider_credentials
        WHERE tenant_id = :tenant_id AND revoked_at IS NULL
    """), {"tenant_id": tenant_id}).mappings().all()
    return {"credentials": [dict(r) for r in rows]}

@router.get("/{credential_id}")
async def get_credential(credential_id: str, tenant_id: str, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    row = db.execute(text("""
        SELECT id, provider, provider_runtime, credential_type, profile_id, account_id, email, display_name, status, expires_at, last_used_at, last_error_code
        FROM provider_credentials
        WHERE id = :id AND tenant_id = :tenant_id AND revoked_at IS NULL
    """), {"id": credential_id, "tenant_id": tenant_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Credential not found")
    return dict(row)

@router.post("/{credential_id}/revoke")
async def revoke_credential(credential_id: str, tenant_id: str, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    res = db.execute(text("""
        UPDATE provider_credentials SET status = 'revoked', revoked_at = now()
        WHERE id = :id AND tenant_id = :tenant_id AND revoked_at IS NULL
    """), {"id": credential_id, "tenant_id": tenant_id})
    db.commit()
    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="Credential not found")
    return {"status": "revoked"}

@router.delete("/{credential_id}")
async def delete_credential(credential_id: str, tenant_id: str, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    # Hard delete or just map to revoke based on compliance. Let's hard delete.
    res = db.execute(text("DELETE FROM provider_credentials WHERE id = :id AND tenant_id = :tenant_id"), {"id": credential_id, "tenant_id": tenant_id})
    db.commit()
    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="Credential not found")
    return {"status": "deleted"}

@router.post("/openai-codex/device/start")
async def start_device_flow(tenant_id: str, db: Session = Depends(get_db), crypto = Depends(get_crypto_service), current_user = Depends(get_current_user)):
    svc = CodexDeviceAuthService(db, crypto)
    return await svc.start_device_flow(tenant_id, str(current_user.get("id", "admin")))

@router.get("/openai-codex/device/status/{session_id}")
async def status_device_flow(session_id: str, tenant_id: str, db: Session = Depends(get_db), crypto = Depends(get_crypto_service), current_user = Depends(get_current_user)):
    svc = CodexDeviceAuthService(db, crypto)
    return await svc.status_device_flow(tenant_id, session_id)

@router.post("/import/openclaw-auth-profile")
async def import_openclaw_profile(payload: dict, tenant_id: str, db: Session = Depends(get_db), crypto = Depends(get_crypto_service), current_user = Depends(get_current_user)):
    importer = CodexAuthProfileImporter(db, crypto)
    cred_id = importer.import_profile(tenant_id, payload, str(current_user.get("id", "admin")))
    return {"id": cred_id}
