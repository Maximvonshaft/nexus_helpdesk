import pytest
from unittest.mock import Mock, AsyncMock, ANY
from app.services.provider_runtime.router import ProviderRuntimeRouter
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult
from app.services.provider_runtime.registry import ProviderRegistry, ProviderAdapter

class DummyAdapter(ProviderAdapter):
    def __init__(self, name, result):
        self.name = name
        self._result = result
    async def generate(self, db, req):
        return self._result

@pytest.mark.asyncio
async def test_provider_runtime_router_fallback_and_audit():
    mock_db = Mock()
    mock_rule = Mock()
    mock_rule.mappings.return_value.first.return_value = {
        'primary_provider': 'failing_provider',
            'fallback_providers': ['success_provider'],
            'output_contract': 'speedaf_webchat_fast_reply_v1',
            'timeout_ms': 3000,
            'kill_switch': False,
            'canary_percent': 100
    }
    
    # We need to distinguish between SELECT rules and INSERT audit logs.
    def mock_db_execute(stmt, params, *args, **kwargs):
        query = str(stmt).lower()
        if "insert into provider_runtime_audit_logs" in query:
            return Mock()
        return mock_rule
        
    mock_db.execute.side_effect = mock_db_execute

    ProviderRegistry.register('failing_provider', lambda db: DummyAdapter('failing_provider', ProviderResult.unavailable('failing_provider', 'error', 10)))
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
    
    # 2 audit logs expected (1 for failed, 1 for success)
    assert mock_db.execute.call_count == 3  # 1 select rule, 2 insert audit
    
@pytest.mark.asyncio
async def test_provider_runtime_router_parse_reject():
    mock_db = Mock()
    mock_rule = Mock()
    mock_rule.mappings.return_value.first.return_value = {
        'primary_provider': 'bad_output_provider',
        'fallback_providers': [],
        'output_contract': 'speedaf_webchat_fast_reply_v1',
        'timeout_ms': 3000,
        'kill_switch': False,
        'canary_percent': 0
    }
    
    def mock_db_execute(stmt, params, *args, **kwargs):
        query = str(stmt).lower()
        if "insert into provider_runtime_audit_logs" in query:
            return Mock()
        return mock_rule
        
    mock_db.execute.side_effect = mock_db_execute

    ProviderRegistry.register('bad_output_provider', lambda db: DummyAdapter('bad_output_provider', ProviderResult(
        ok=True, provider='bad_output_provider', elapsed_ms=100, 
        structured_output={"customer_reply": "hi"} # missing required fields
    )))

    router = ProviderRuntimeRouter(mock_db)
    req = ProviderRequest(
        request_id="req1", tenant_id="t1", tenant_key="tk1", channel_key="c1",
        session_id="s1", scenario="webchat", body="hello", output_contract="contract", timeout_ms=1000
    )

    res = await router.route(req)
    assert not res.ok
    assert res.error_code == "all_providers_failed"
    
    # Audit calls
    # 1 for select rules, 1 for parse_reject insert, 1 for all_providers_failed insert
    assert mock_db.execute.call_count == 3
