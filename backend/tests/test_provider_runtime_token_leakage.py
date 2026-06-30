import pytest
import json
from app.services.provider_runtime.schemas import ProviderResult

def test_provider_runtime_token_leakage():
    # Verify that a ProviderResult cannot inadvertently dump tokens
    # Because there are no token fields!
    res = ProviderResult(ok=True, provider="codex", elapsed_ms=10, structured_output={"a": 1})
    
    dump = json.dumps(res.model_dump())
    assert "token" not in dump.lower()
    
    # Check that Fingerprint masks token
    from app.services.provider_runtime.credential_crypto import CredentialCryptoService
    from cryptography.fernet import Fernet
    crypto = CredentialCryptoService(env_key=Fernet.generate_key().decode('utf-8'))
    fp = crypto.get_safe_fingerprint("p", "t", "c", "my-secret-token-12345")
    assert "my-secret" not in fp
    assert "token" not in fp
