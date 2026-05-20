import time
from sqlalchemy.orm import Session
from ..schemas import ProviderCapabilities, ProviderRequest, ProviderResult
from ..registry import ProviderAdapter

class OpenAIResponsesAdapter(ProviderAdapter):
    name = "openai_responses"
    capabilities = ProviderCapabilities(
        fast_reply=True,
        structured_output=True,
        safety_level="standard"
    )

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def generate(self, db: Session, request: ProviderRequest) -> ProviderResult:
        started = time.monotonic()
        if not self.api_key:
            return ProviderResult.unavailable(self.name, "not_configured", 0)

        # Skeleton for Phase 1 as requested.
        return ProviderResult.unavailable(self.name, "openai_adapter_not_fully_implemented", int((time.monotonic() - started) * 1000))
