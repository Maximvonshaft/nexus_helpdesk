import json
import time
import httpx
from sqlalchemy.orm import Session
from sqlalchemy import text
from ..schemas import ProviderCapabilities, ProviderRequest, ProviderResult
from ..registry import ProviderAdapter
from ..credential_crypto import CredentialCryptoService
from ..oauth_refresh_manager import OAuthRefreshManager

class CodexAppServerAdapter(ProviderAdapter):
    name = "codex_app_server"
    capabilities = ProviderCapabilities(
        fast_reply=True,
        structured_output=True,
        handoff_decision=True,
        safety_level="reply_only"
    )

    def __init__(self, crypto_service: CredentialCryptoService, bridge_url: str):
        self.crypto_service = crypto_service
        self.bridge_url = bridge_url

    async def generate(self, db: Session, request: ProviderRequest) -> ProviderResult:
        started = time.monotonic()
        refresh_manager = OAuthRefreshManager(db, self.crypto_service)
        
        cred_row = db.execute(text("""
            SELECT id, account_id, chatgpt_plan_type 
            FROM provider_credentials 
            WHERE tenant_id = :tenant_id AND provider = 'openai-codex' AND status = 'active'
            ORDER BY created_at DESC LIMIT 1
        """), {"tenant_id": request.tenant_id}).mappings().first()

        if not cred_row:
            return ProviderResult.unavailable(self.name, "no_active_credential", int((time.monotonic() - started) * 1000))

        access_token = await refresh_manager.get_valid_access_token(request.tenant_id, cred_row['id'])
        if not access_token:
            return ProviderResult.unavailable(self.name, "credential_error", int((time.monotonic() - started) * 1000))

        auth_tokens_payload = {
            "type": "chatgptAuthTokens",
            "accessToken": access_token,
            "chatgptAccountId": cred_row['account_id'],
            "chatgptPlanType": cred_row['chatgpt_plan_type']
        }

        bridge_req = {
            "auth": auth_tokens_payload,
            "messages": request.recent_context or [],
            "body": request.body,
            "contract": request.output_contract
        }

        try:
            async with httpx.AsyncClient(timeout=request.timeout_ms / 1000.0) as client:
                resp = await client.post(self.bridge_url, json=bridge_req)
                resp.raise_for_status()
                data = resp.json()
                
                return ProviderResult(
                    ok=True,
                    provider=self.name,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    structured_output=data,
                    raw_payload_safe_summary={"bridge_status": resp.status_code}
                )
        except httpx.TimeoutException:
            return ProviderResult.unavailable(self.name, "bridge_timeout", int((time.monotonic() - started) * 1000))
        except httpx.HTTPError as e:
            return ProviderResult.unavailable(self.name, "bridge_error", int((time.monotonic() - started) * 1000))
        except Exception:
            return ProviderResult.unavailable(self.name, "internal_error", int((time.monotonic() - started) * 1000))
