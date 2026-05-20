import os
import hashlib
from cryptography.fernet import Fernet

class CredentialCryptoService:
    def __init__(self, key_file: str = "/run/nexus/provider_credential_key", env_key: str = None):
        key_data = None
        is_prod = os.environ.get("ENV", "development") == "production"

        if os.path.exists(key_file):
            with open(key_file, "rb") as f:
                key_data = f.read().strip()
        elif env_key:
            if is_prod:
                raise RuntimeError("production 禁止明文 PROVIDER_CREDENTIAL_ENCRYPTION_KEY. Must use FILE.")
            key_data = env_key.encode('utf-8')
            
        if not key_data:
            if is_prod:
                raise RuntimeError("PROVIDER_CREDENTIAL_ENCRYPTION_KEY missing in production")
            # In dev, we use a dummy key for tests if not provided
            key_data = Fernet.generate_key()
            
        try:
            self._fernet = Fernet(key_data)
        except ValueError:
            raise RuntimeError("Invalid PROVIDER_CREDENTIAL_ENCRYPTION_KEY format")

    def encrypt(self, data: str) -> str:
        if not data:
            return None
        return self._fernet.encrypt(data.encode('utf-8')).decode('utf-8')

    def decrypt(self, encrypted_data: str) -> str:
        if not encrypted_data:
            return None
        return self._fernet.decrypt(encrypted_data.encode('utf-8')).decode('utf-8')

    def get_safe_fingerprint(self, provider: str, tenant_id: str, credential_id: str, secret: str) -> str:
        if not secret:
            return None
        raw = f"{provider}:{tenant_id}:{credential_id}:{secret}"
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()
