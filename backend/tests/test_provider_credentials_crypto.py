import pytest
import os
from app.services.provider_runtime.credential_crypto import CredentialCryptoService

def test_credential_crypto_service_encryption():
    os.environ["ENV"] = "test"
    crypto = CredentialCryptoService(env_key="1234567890123456789012345678901234567890123=") # Invalid fernet key format
    # Let's generate a valid fernet key
    from cryptography.fernet import Fernet
    valid_key = Fernet.generate_key().decode('utf-8')
    crypto = CredentialCryptoService(env_key=valid_key)
    
    plain_text = "test-secret-token"
    encrypted = crypto.encrypt(plain_text)
    assert encrypted != plain_text
    assert crypto.decrypt(encrypted) == plain_text
    
    assert crypto.encrypt(None) is None
    assert crypto.decrypt(None) is None

def test_safe_fingerprint():
    from cryptography.fernet import Fernet
    valid_key = Fernet.generate_key().decode('utf-8')
    crypto = CredentialCryptoService(env_key=valid_key)
    
    fp = crypto.get_safe_fingerprint("openai-codex", "tenant_1", "cred_1", "secret")
    assert fp is not None
    assert "secret" not in fp
