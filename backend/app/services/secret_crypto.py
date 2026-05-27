from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet


class SecretCryptoService:
    """Encrypt application-managed secrets with a purpose-specific Fernet key."""

    def __init__(
        self,
        *,
        purpose: str,
        key_file_env: str,
        key_env: str,
        default_prod_key_path: str | None = None,
        key_file: str | None = None,
        env_key: str | None = None,
    ) -> None:
        app_env = os.environ.get("APP_ENV", os.environ.get("ENV", "development")).strip().lower()
        is_prod = app_env == "production"
        configured_key_file = key_file or os.environ.get(key_file_env)
        configured_env_key = env_key if env_key is not None else os.environ.get(key_env)

        key_data: bytes | None = None
        if configured_key_file:
            key_path = Path(configured_key_file)
            if key_path.exists():
                key_data = key_path.read_bytes().strip()
            elif is_prod:
                raise RuntimeError(f"{key_file_env} is configured but the file does not exist")
        elif is_prod and default_prod_key_path:
            default_path = Path(default_prod_key_path)
            if default_path.exists():
                key_data = default_path.read_bytes().strip()

        if key_data is None and configured_env_key:
            if is_prod:
                raise RuntimeError(f"production environment prohibits plain text {key_env}; use {key_file_env}")
            key_data = configured_env_key.encode("utf-8")

        if key_data is None:
            if is_prod:
                raise RuntimeError(f"{key_file_env} is required in production")
            key_data = self._deterministic_dev_key(purpose)

        try:
            self._fernet = Fernet(key_data)
        except Exception as exc:
            raise RuntimeError(f"Invalid Fernet key format for {purpose}") from exc

    @staticmethod
    def _deterministic_dev_key(purpose: str) -> bytes:
        digest = hashlib.sha256(f"nexus-dev-default-{purpose}-key-v1".encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)

    @classmethod
    def outbound_email(cls) -> "SecretCryptoService":
        return cls(
            purpose="outbound-email",
            key_file_env="OUTBOUND_EMAIL_ENCRYPTION_KEY_FILE",
            key_env="OUTBOUND_EMAIL_ENCRYPTION_KEY",
            default_prod_key_path="/run/nexus/outbound_email_encryption_key",
        )

    def encrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        return self._fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, encrypted_value: str | None) -> str | None:
        if not encrypted_value:
            return None
        return self._fernet.decrypt(encrypted_value.encode("utf-8")).decode("utf-8")


def mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    return "********"

