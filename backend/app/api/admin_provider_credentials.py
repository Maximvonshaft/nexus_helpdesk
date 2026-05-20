from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..db import get_db
from ..services.provider_runtime.codex_device_auth_service import CodexDeviceAuthService
from ..services.provider_runtime.credential_crypto import CredentialCryptoService
from ..services.provider_runtime.codex_auth_profile_importer import CodexAuthProfileImporter

router = APIRouter()

def get_crypto_service():
    return CredentialCryptoService()

@router.post("/openai-codex/device/start")
async def start_device_flow(tenant_id: str = "default", user_id: str = "admin", db: Session = Depends(get_db), crypto = Depends(get_crypto_service)):
    svc = CodexDeviceAuthService(db, crypto)
    return await svc.start_device_flow(tenant_id, user_id)

@router.get("/openai-codex/device/status/{session_id}")
async def status_device_flow(session_id: str, tenant_id: str = "default", db: Session = Depends(get_db), crypto = Depends(get_crypto_service)):
    svc = CodexDeviceAuthService(db, crypto)
    return await svc.status_device_flow(tenant_id, session_id)

@router.post("/openai-codex/device/cancel")
async def cancel_device_flow(session_id: str):
    # Skeleton
    return {"status": "cancelled"}

@router.post("/import/openclaw-auth-profile")
async def import_openclaw_profile(payload: dict, tenant_id: str = "default", user_id: str = "admin", db: Session = Depends(get_db), crypto = Depends(get_crypto_service)):
    importer = CodexAuthProfileImporter(db, crypto)
    cred_id = importer.import_profile(tenant_id, payload, user_id)
    return {"id": cred_id}
