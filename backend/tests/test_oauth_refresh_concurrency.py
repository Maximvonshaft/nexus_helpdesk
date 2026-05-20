import pytest
import asyncio
from unittest.mock import Mock, AsyncMock
from app.services.provider_runtime.oauth_refresh_manager import OAuthRefreshManager
from datetime import datetime, timezone, timedelta

@pytest.mark.asyncio
async def test_oauth_refresh_concurrency():
    mock_db = Mock()
    mock_crypto = Mock()
    mock_crypto.decrypt.side_effect = lambda x: x 
    mock_crypto.encrypt.side_effect = lambda x: x
    
    mgr = OAuthRefreshManager(mock_db, mock_crypto)
    
    mgr._perform_http_refresh = AsyncMock(return_value=("new_access", "new_refresh", 3600))
    
    now = datetime.now(timezone.utc)
    expired_time = now - timedelta(minutes=10)
    
    lock_read_count = 0
    def mock_db_execute(stmt, *args, **kwargs):
        query_str = str(stmt).lower()
        if "pg_advisory" in query_str or "update provider_credentials" in query_str:
            return Mock()
            
        mock_result = Mock()
        if "encrypted_refresh_token" in query_str:
            mock_result.mappings.return_value.first.return_value = {
                'status': 'active', 'expires_at': expired_time,
                'encrypted_access_token': 'old_access', 'encrypted_refresh_token': 'old_refresh', 'provider': 'openai-codex'
            }
        else:
            nonlocal lock_read_count
            lock_read_count += 1
            if lock_read_count == 1:
                mock_result.mappings.return_value.first.return_value = {
                    'status': 'active', 'expires_at': expired_time,
                    'encrypted_access_token': 'old_access', 'encrypted_refresh_token': 'old_refresh', 'provider': 'openai-codex'
                }
            else:
                mock_result.mappings.return_value.first.return_value = {
                    'status': 'active', 'expires_at': now + timedelta(minutes=50),
                    'encrypted_access_token': 'new_access', 'encrypted_refresh_token': 'new_refresh', 'provider': 'openai-codex'
                }
        return mock_result
        
    mock_db.execute.side_effect = mock_db_execute
    
    # Mock dialect to bypass lock error for tests
    mock_bind = Mock()
    mock_bind.dialect.name = "sqlite"
    mock_db.get_bind.return_value = mock_bind
    
    async def simulate_req():
        return await mgr.get_valid_access_token("tenant_1", "cred_1")
        
    tasks = [simulate_req() for _ in range(10)]
    results = await asyncio.gather(*tasks)
    
    assert all(r == "new_access" for r in results)
    mgr._perform_http_refresh.assert_called_once()
