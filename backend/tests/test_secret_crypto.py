from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

os.environ.setdefault("APP_ENV", "development")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.services.secret_crypto import SecretCryptoService, mask_secret  # noqa: E402


def test_outbound_email_secret_crypto_roundtrip_with_file_key(tmp_path, monkeypatch):
    key_file = tmp_path / "email.key"
    key_file.write_bytes(Fernet.generate_key())
    monkeypatch.setenv("OUTBOUND_EMAIL_ENCRYPTION_KEY_FILE", str(key_file))
    monkeypatch.delenv("OUTBOUND_EMAIL_ENCRYPTION_KEY", raising=False)

    crypto = SecretCryptoService.outbound_email()
    encrypted = crypto.encrypt("smtp-password")

    assert encrypted != "smtp-password"
    assert "smtp-password" not in encrypted
    assert crypto.decrypt(encrypted) == "smtp-password"
    assert mask_secret(encrypted) == "********"


def test_outbound_email_secret_crypto_fails_closed_in_production_without_key(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("OUTBOUND_EMAIL_ENCRYPTION_KEY_FILE", raising=False)
    monkeypatch.delenv("OUTBOUND_EMAIL_ENCRYPTION_KEY", raising=False)

    with pytest.raises(RuntimeError, match="OUTBOUND_EMAIL_ENCRYPTION_KEY_FILE is required"):
        SecretCryptoService.outbound_email()


def test_outbound_email_secret_crypto_rejects_plain_env_key_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("OUTBOUND_EMAIL_ENCRYPTION_KEY_FILE", raising=False)
    monkeypatch.setenv("OUTBOUND_EMAIL_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))

    with pytest.raises(RuntimeError, match="prohibits plain text"):
        SecretCryptoService.outbound_email()
