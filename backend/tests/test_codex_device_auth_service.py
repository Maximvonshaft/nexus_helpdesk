import pytest
import os
from unittest.mock import Mock
from app.services.provider_runtime.codex_device_auth_service import CodexDeviceAuthService

@pytest.mark.asyncio
async def test_codex_device_auth_service():
    os.environ["CODEX_OAUTH_DEVICE_FLOW_ENABLED"] = "true"
    mock_db = Mock()
    mock_crypto = Mock()
    
    svc = CodexDeviceAuthService(mock_db, mock_crypto)
    res = await svc.start_device_flow("tenant_1", "user_1")
    
    assert "user_code" in res
    assert "verification_url" in res
    
    mock_db.execute.assert_called_once()
    mock_db.commit.assert_called_once()

@pytest.mark.asyncio
async def test_codex_device_auth_service_disabled():
    os.environ["CODEX_OAUTH_DEVICE_FLOW_ENABLED"] = "false"
    mock_db = Mock()
    mock_crypto = Mock()
    
    svc = CodexDeviceAuthService(mock_db, mock_crypto)
    with pytest.raises(ValueError, match="Device flow is disabled"):
        await svc.start_device_flow("tenant_1", "user_1")
