from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet


class CredentialCryptoService:
    """Encrypt and decrypt provider credentials with a stable server-side key.

    Production must load the Fernet key from PROVIDER_CREDENTIAL_ENCRYPTION_KEY_FILE.
    Development/test can pass env_key explicitly or fall back to a deterministic dev-only key
    so local restarts do not make previously encrypted fixtures unreadable.
    """

    def __init__(self, key_file: str | None = None, env_key: str | None = None):
        app_env = os.environ.get("APP_ENV", os.environ.get("ENV", "development")).strip().lower()
        is_prod = app_env == "production"

        configured_key_file = key_file or os.environ.get("PROVIDER_CREDENTIAL_ENCRYPTION_KEY_FILE")
        configured_env_key = env_key if env_key is not None else os.environ.get("PROVIDER_CREDENTIAL_ENCRYPTION_KEY")

        key_data: bytes | None = None
        if configured_key_file:
            key_path = Path(configured_key_file)
            if key_path.exists():
                key_data = key_path.read_bytes().strip()
            elif is_prod:
                raise RuntimeError("PROVIDER_CREDENTIAL_ENCRYPTION_KEY_FILE is configured but the file does not exist")
        elif is_prod:
            # Keep the old deployment default for production, but fail closed if absent.
            default_path = Path("/run/nexus/provider_credential_key")
            if default_path.exists():
                key_data = default_path.read_bytes().strip()

        if key_data is None and configured_env_key:
            if is_prod:
                raise RuntimeError("production environment prohibits plain text PROVIDER_CREDENTIAL_ENCRYPTION_KEY. Use PROVIDER_CREDENTIAL_ENCRYPTION_KEY_FILE")
            key_data = configured_env_key.encode("utf-8")

        if key_data is None:
            if is_prod:
                raise RuntimeError("PROVIDER_CREDENTIAL_ENCRYPTION_KEY_FILE is required in production")
            key_data = self._deterministic_dev_key()

        try:
            self._fernet = Fernet(key_data)
        except Exception as exc:
            raise RuntimeError("Invalid provider credential Fernet key format") from exc

    @staticmethod
    def _deterministic_dev_key() -> bytes:
        digest = hashlib.sha256(b"nexus-dev-default-credential-key-v1").digest()
        return base64.urlsafe_b64encode(digest)

    def encrypt(self, data: str | None) -> str | None:
        if not data:
            return None
        return self._fernet.encrypt(data.encode("utf-8")).decode("utf-8")

    def decrypt(self, encrypted_data: str | None) -> str | None:
        if not encrypted_data:
            return None
        return self._fernet.decrypt(encrypted_data.encode("utf-8")).decode("utf-8")

    def get_safe_fingerprint(self, provider: str, tenant_id: str, credential_id: str, secret: str | None) -> str | None:
        if not secret:
            return None
        raw = f"{provider}:{tenant_id}:{credential_id}:{secret}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
