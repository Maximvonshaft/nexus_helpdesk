import pytest
from unittest.mock import Mock
from app.services.provider_runtime.codex_auth_profile_importer import CodexAuthProfileImporter

def test_codex_auth_profile_importer():
    mock_db = Mock()
    mock_crypto = Mock()
    mock_crypto.encrypt.side_effect = lambda x: x
    mock_crypto.get_safe_fingerprint.return_value = "fingerprint_123"
    
    importer = CodexAuthProfileImporter(mock_db, mock_crypto)
    
    profile_data = {
        "type": "oauth",
        "provider": "openai-codex",
        "access": "access_token_123",
        "refresh": "refresh_token_123",
        "expires": "2026-05-21T00:00:00Z",
        "accountId": "acc_123",
        "chatgptPlanType": "plus"
    }
    
    cred_id = importer.import_profile("tenant_1", profile_data, "admin_user")
    
    assert cred_id is not None
    mock_db.execute.assert_called_once()
    mock_db.commit.assert_called_once()
