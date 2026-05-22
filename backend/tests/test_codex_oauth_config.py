from __future__ import annotations

import pytest

from app.services.provider_runtime.codex_oauth_config import CodexOAuthConfig


def test_codex_oauth_scope_allowlist(monkeypatch):
    monkeypatch.setenv("CODEX_OAUTH_ALLOWED_SCOPES", "profile.read,reply.write")
    monkeypatch.setenv("CODEX_OAUTH_DEFAULT_SCOPES", "profile.read")
    config = CodexOAuthConfig.from_env()
    assert config.normalize_scope(None) == "profile.read"
    assert config.normalize_scope(["reply.write", "profile.read", "reply.write"]) == "reply.write profile.read"
    with pytest.raises(ValueError, match="not allowlisted"):
        config.normalize_scope(["admin.full"])


def test_codex_oauth_requires_backend_secret_file_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CODEX_OAUTH_CLIENT_SECRET", "plain-secret")
    with pytest.raises(RuntimeError, match="forbidden in production"):
        CodexOAuthConfig.from_env()
