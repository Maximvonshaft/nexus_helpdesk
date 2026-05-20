import json
import time
import httpx
import os
import urllib.parse
import ipaddress
from sqlalchemy.orm import Session
from sqlalchemy import text
from ..schemas import ProviderCapabilities, ProviderRequest, ProviderResult
from ..registry import ProviderAdapter
from ..credential_crypto import CredentialCryptoService
from ..oauth_refresh_manager import OAuthRefreshManager

class CodexAppServerAdapter(ProviderAdapter):
    name = "codex_app_server"
    capabilities = ProviderCapabilities(
        fast_reply=True, structured_output=True, handoff_decision=True, safety_level="reply_only"
    )

    def __init__(self, crypto_service: CredentialCryptoService, bridge_url: str):
        self.crypto_service = crypto_service
        self.bridge_url = bridge_url
        self._validate_bridge_url(bridge_url)
        self.shared_token = os.environ.get("CODEX_APP_SERVER_TOKEN", "")

    def _validate_bridge_url(self, url: str):
        if not url:
            return
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            raise ValueError(f"Invalid bridge URL: {url}")
            
        # Basic check for localhost / private IPs. 
        # In a real environment, you'd resolve DNS first to prevent rebinding, but for this constraint we check the literal.
        if hostname in ("localhost", "127.0.0.1", "::1"):
            return
            
        try:
            ip = ipaddress.ip_address(hostname)
            # Private IPs (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16) and Tailscale (100.64.0.0/10)
            if ip.is_private or ip.is_loopback:
                return
            
            # Check tailnet CGNAT range explicitly just in case is_private is false for it on some python versions
            if ip.version == 4 and int(ip) & 0xffc00000 == 0x64400000: # 100.64.0.0/10
                return
                
            raise ValueError(f"Bridge URL must be private, loopback, or tailnet. Got public IP: {hostname}")
        except ValueError as e:
            if "Bridge URL must be private" in str(e):
                raise
            # If it's a hostname (e.g. "bridge.local"), we assume it's safe if it doesn't contain public TLDs,
            # but to be strict per requirements: "private / loopback / tailnet", we reject unknown public hosts.
            if "." in hostname and not hostname.endswith(".local") and not hostname.endswith(".internal"):
                # For safety in this strict rule, fail if it looks like a public domain
                raise ValueError(f"Bridge URL looks like a public domain: {hostname}. Must be private/loopback/tailnet IP or .local")

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

        # Step 1: Explicit login/start to the bridge to establish a session, or pass it separated
        # We pass it in a structured way that the bridge explicitly uses as a "login" vs "body"
        bridge_req = {
            "login": auth_tokens_payload, # Explicitly separated login boundary
            "messages": request.recent_context or [],
            "body": request.body,
            "contract": request.output_contract
        }

        headers = {}
        if self.shared_token:
            headers["Authorization"] = f"Bearer {self.shared_token}"

        try:
            async with httpx.AsyncClient(timeout=request.timeout_ms / 1000.0) as client:
                resp = await client.post(self.bridge_url, json=bridge_req, headers=headers)
                resp.raise_for_status()
                return ProviderResult(
                    ok=True, provider=self.name, elapsed_ms=int((time.monotonic() - started) * 1000),
                    structured_output=resp.json(), raw_payload_safe_summary={"bridge_status": resp.status_code}
                )
        except httpx.TimeoutException:
            return ProviderResult.unavailable(self.name, "bridge_timeout", int((time.monotonic() - started) * 1000))
        except httpx.HTTPError as e:
            return ProviderResult.unavailable(self.name, "bridge_error", int((time.monotonic() - started) * 1000))
        except Exception:
            return ProviderResult.unavailable(self.name, "internal_error", int((time.monotonic() - started) * 1000))
