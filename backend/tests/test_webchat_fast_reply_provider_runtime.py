import pytest
from unittest.mock import patch

from app.services.ai_runtime.schemas import FastAIProviderRequest
from app.services.provider_runtime.webchat_fast_dispatcher import dispatch_webchat_fast_reply


@pytest.mark.asyncio
async def test_dispatch_webchat_fast_reply_with_provider_runtime():
    req = FastAIProviderRequest(
        tenant_key="tenant_1",
        channel_key="webchat",
        session_id="session_1",
        body="Where is my package?",
        recent_context=[],
        request_id="req1",
        tracking_fact_summary="In transit",
        tracking_fact_metadata={"number": "123"},
        tracking_fact_evidence_present=True,
    )

    with patch("app.services.provider_runtime.webchat_fast_dispatcher.ProviderRuntimeRouter.route") as mock_route:
        async def mock_route_fn(pr_req):
            from app.services.provider_runtime.schemas import ProviderResult

            return ProviderResult(
                ok=True,
                provider="codex_app_server",
                elapsed_ms=150,
                structured_output={
                    "customer_reply": "It's on the way.",
                    "intent": "tracking",
                    "tracking_number": "123",
                    "handoff_required": False,
                    "ticket_should_create": False,
                },
                raw_payload_safe_summary={"safe": True},
            )

        mock_route.side_effect = mock_route_fn

        with patch("app.services.provider_runtime.webchat_fast_dispatcher.SessionLocal"):
            res = await dispatch_webchat_fast_reply(request=req)

            assert res.ok
            assert res.ai_generated
            assert res.reply == "It's on the way."
            assert res.intent == "tracking"
            assert res.tracking_number == "123"
            assert res.reply_source == "codex_app_server"
            mock_route.assert_called_once()
