import os
from sqlalchemy.orm import Session
from .schemas import ProviderRequest, ProviderResult, ProviderCapabilities
from .router import ProviderRuntimeRouter
from .registry import ProviderRegistry
from .credential_crypto import CredentialCryptoService
from .oauth_refresh_manager import OAuthRefreshManager
from .codex_device_auth_service import CodexDeviceAuthService
from .codex_auth_profile_importer import CodexAuthProfileImporter

from .adapters.codex_app_server import CodexAppServerAdapter
from .adapters.openai_responses import OpenAIResponsesAdapter

def bootstrap_provider_runtime():
    crypto = CredentialCryptoService()
    
    bridge_url = os.environ.get("CODEX_APP_SERVER_BRIDGE_URL", "http://127.0.0.1:18794/reply")
    
    ProviderRegistry.register("codex_app_server", lambda db: CodexAppServerAdapter(crypto, bridge_url))
    
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    ProviderRegistry.register("openai_responses", lambda db: OpenAIResponsesAdapter(openai_key))
    
    # Register skeletons
    from .registry import ProviderAdapter
    class SkeletonAdapter(ProviderAdapter):
        def __init__(self, name):
            self.name = name
            
        async def generate(self, db, req):
            return ProviderResult.unavailable(self.name, f"{self.name}_skeleton_unavailable", 0)
            
    ProviderRegistry.register("anthropic", lambda db: SkeletonAdapter("anthropic"))
    ProviderRegistry.register("gemini", lambda db: SkeletonAdapter("gemini"))
    ProviderRegistry.register("openrouter", lambda db: SkeletonAdapter("openrouter"))
    ProviderRegistry.register("rule_engine", lambda db: SkeletonAdapter("rule_engine"))
    
bootstrap_provider_runtime()
