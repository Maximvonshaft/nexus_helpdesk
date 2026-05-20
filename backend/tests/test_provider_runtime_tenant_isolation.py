import pytest
from unittest.mock import Mock, AsyncMock
from app.services.provider_runtime.adapters.codex_app_server import CodexAppServerAdapter
from app.services.provider_runtime.schemas import ProviderRequest

@pytest.mark.asyncio
async def test_provider_runtime_tenant_isolation():
    mock_db = Mock()
    mock_crypto = Mock()
    adapter = CodexAppServerAdapter(mock_crypto, "http://bridge")
    
    # We want to intercept the db.execute call to check the parameters
    def mock_db_execute(stmt, params, *args, **kwargs):
        assert params["tenant_id"] == "tenant_A"
        mock_res = Mock()
        mock_res.mappings.return_value.first.return_value = None # force fail
        return mock_res
        
    mock_db.execute.side_effect = mock_db_execute
    
    req = ProviderRequest(
        request_id="req1",
        tenant_id="tenant_A",
        tenant_key="tenant_A",
        channel_key="webchat",
        session_id="sess1",
        scenario="test",
        body="hello",
        output_contract="test_contract",
        timeout_ms=1000
    )
    
    res = await adapter.generate(mock_db, req)
    assert not res.ok
    assert res.error_code == "no_active_credential"
    mock_db.execute.assert_called_once()
