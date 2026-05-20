import pytest
from unittest.mock import Mock, AsyncMock
from app.services.provider_runtime.router import ProviderRuntimeRouter
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult, ProviderCapabilities
from app.services.provider_runtime.registry import ProviderRegistry, ProviderAdapter

class DummyAdapter(ProviderAdapter):
    def __init__(self, name, result):
        self.name = name
        self._result = result
    async def generate(self, db, req):
        return self._result

@pytest.mark.asyncio
async def test_provider_runtime_router_fallback():
    mock_db = Mock()
    # Mock routing rule
    mock_rule = Mock()
    mock_rule.mappings.return_value.first.return_value = {
        'primary_provider': 'failing_provider',
        'fallback_providers': ['success_provider'],
        'output_contract': 'speedaf_webchat_fast_reply_v1',
        'timeout_ms': 3000,
        'kill_switch': False
    }
    mock_db.execute.return_value = mock_rule

    ProviderRegistry.register('failing_provider', lambda db: DummyAdapter('failing_provider', ProviderResult.unavailable('failing_provider', 'error', 0)))
    ProviderRegistry.register('success_provider', lambda db: DummyAdapter('success_provider', ProviderResult(
        ok=True, provider='success_provider', elapsed_ms=100, 
        structured_output={"customer_reply": "hi", "language": "en", "intent": "greeting", "handoff_required": False, "ticket_should_create": False}
    )))

    router = ProviderRuntimeRouter(mock_db)
    req = ProviderRequest(
        request_id="req1", tenant_id="t1", tenant_key="tk1", channel_key="c1",
        session_id="s1", scenario="webchat", body="hello", output_contract="contract", timeout_ms=1000
    )

    res = await router.route(req)
    assert res.ok
    assert res.provider == "success_provider"
    assert res.structured_output["customer_reply"] == "hi"
