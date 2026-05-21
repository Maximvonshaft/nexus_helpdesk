import pytest
from unittest.mock import patch, Mock
import asyncio
from app.services.ai_runtime.provider_router import generate_fast_reply
from app.services.ai_runtime.schemas import FastAIProviderRequest
from app.services.webchat_fast_config import get_webchat_fast_settings

@pytest.mark.asyncio
async def test_generate_fast_reply_with_provider_runtime():
    settings = get_webchat_fast_settings()
    # We patch the property just by replacing it since we don't care about frozen or we can mock it
    # Actually it's probably frozen or property, let's mock it
    mock_settings = Mock(provider="provider_runtime", fallback_provider="rule_engine")
    
    req = FastAIProviderRequest(
        tenant_key="tenant_1",
        channel_key="webchat",
        session_id="session_1",
        body="Where is my package?",
        recent_context=[],
        request_id="req1",
        tracking_fact_summary="In transit",
        tracking_fact_metadata={"number": "123"},
        tracking_fact_evidence_present=True
    )
    
    with patch("app.services.ai_runtime.provider_router.ProviderRuntimeRouter.route") as mock_route:
        async def mock_route_fn(pr_req):
            from app.services.provider_runtime.schemas import ProviderResult
            return ProviderResult(
                ok=True, provider="codex_app_server", elapsed_ms=150,
                structured_output={
                    "customer_reply": "It's on the way.",
                    "intent": "tracking",
                    "tracking_number": "123",
                    "handoff_required": False,
                    "ticket_should_create": False
                },
                raw_payload_safe_summary={"safe": True}
            )
        mock_route.side_effect = mock_route_fn
        
        with patch("app.services.ai_runtime.provider_router.SessionLocal") as mock_db:
            res = await generate_fast_reply(request=req, settings=mock_settings)
            
            assert res.ok
            assert res.ai_generated
            assert res.reply == "It's on the way."
            assert res.intent == "tracking"
            assert res.tracking_number == "123"
            assert res.reply_source == "codex_app_server"
            mock_route.assert_called_once()
