from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.settings import Settings


@contextmanager
def patched_env(values: dict[str, str]):
    old_values = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key, old in old_values.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def production_env(**overrides: str) -> dict[str, str]:
    key_name = "SECRET" + "_KEY"
    env = {
        "APP_ENV": "production",
        "NEXUS_PROCESS_ROLE": "web",
        key_name: "ci-value-for-production-settings-contract",
        "DATABASE_URL": "postgresql+psycopg://helpdesk:helpdesk@db:5432/helpdesk",
        "ALLOWED_ORIGINS": "https://example.test",
        "AUTO_INIT_DB": "false",
        "SEED_DEMO_DATA": "false",
        "ALLOW_DEV_AUTH": "false",
        "ALLOW_LEGACY_INTEGRATION_API_KEY": "false",
        "EXTERNAL_CHANNEL_CLI_FALLBACK_ENABLED": "false",
        "STORAGE_BACKEND": "s3",
        "EXTERNAL_CHANNEL_TRANSPORT": "disabled",
        "EXTERNAL_CHANNEL_DEPLOYMENT_MODE": "disabled",
        "WEBCHAT_RATE_LIMIT_BACKEND": "database",
        "WEBCHAT_AI_ENABLED": "true",
        "WEBCHAT_AI_AUTO_REPLY_MODE": "runtime",
        "PROVIDER_RUNTIME_ENABLED": "false",
        "PRIVATE_AI_RUNTIME_ENABLED": "false",
        "WEBCHAT_ALLOWED_ORIGINS": "https://example.test",
        "WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT": "false",
        "WEBCHAT_WS_ENABLED": "false",
        "WEBCHAT_WS_ADMIN_ENABLED": "false",
        "WEBCHAT_WS_PUBLIC_ENABLED": "false",
        "WEBCHAT_WS_BROKER": "database",
        "KNOWLEDGE_EMBEDDINGS_ENABLED": "true",
        "KNOWLEDGE_EMBEDDING_PROVIDER": "openai_compatible",
        "KNOWLEDGE_EMBEDDING_MODEL": "text-embedding-3-small",
        "KNOWLEDGE_EMBEDDING_API_KEY_FILE": "/run/secrets/knowledge_embedding_api_key",
    }
    env.update(overrides)
    return env


def _frontend_exists(monkeypatch) -> None:
    real_exists = Path.exists

    def fake_exists(path):
        if path.name == "index.html" and path.parent.name == "frontend_dist":
            return True
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", fake_exists)


def _clear_web_and_ai_secrets(monkeypatch) -> None:
    for key in (
        "SECRET_KEY",
        "METRICS_TOKEN",
        "ALLOWED_ORIGINS",
        "WEBCHAT_ALLOWED_ORIGINS",
        "KNOWLEDGE_EMBEDDING_API_KEY",
        "KNOWLEDGE_EMBEDDING_API_KEY_FILE",
        "RUNTIME_CONTRACT_SIGNING_SECRET",
        "PRIVATE_AI_RUNTIME_TOKEN_FILE",
        "LIVE_VOICE_UPSTREAM_TOKEN_FILE",
    ):
        monkeypatch.delenv(key, raising=False)


def test_production_settings_accept_hardened_web_contract(monkeypatch):
    _frontend_exists(monkeypatch)

    with patched_env(production_env()):
        settings = Settings()

    assert settings.app_env == "production"
    assert settings.process_role == "web"
    assert settings.is_http_process is True
    assert settings.is_postgres is True
    assert settings.allow_dev_auth is False
    assert settings.webchat_knowledge_reply_mode == "ai_grounded"
    assert settings.webchat_ws_enabled is False
    assert settings.webchat_ws_broker == "database"


@pytest.mark.parametrize(
    ("key", "value", "expected_message"),
    [
        ("SECRET" + "_KEY", "change-me", "SECRET_KEY"),
        ("DATABASE_URL", "sqlite:///tmp.db", "PostgreSQL"),
        ("ALLOW_DEV_AUTH", "true", "ALLOW_DEV_AUTH"),
        (
            "ALLOW_LEGACY_INTEGRATION_API_KEY",
            "true",
            "ALLOW_LEGACY_INTEGRATION_API_KEY",
        ),
        (
            "EXTERNAL_CHANNEL_CLI_FALLBACK_ENABLED",
            "true",
            "EXTERNAL_CHANNEL_CLI_FALLBACK_ENABLED",
        ),
        ("EXTERNAL_CHANNEL_TRANSPORT", "mcp", "EXTERNAL_CHANNEL_TRANSPORT"),
        (
            "EXTERNAL_CHANNEL_DEPLOYMENT_MODE",
            "remote_gateway",
            "EXTERNAL_CHANNEL_DEPLOYMENT_MODE",
        ),
        (
            "WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT",
            "true",
            "WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT",
        ),
        (
            "WEBCHAT_KNOWLEDGE_REPLY_MODE",
            "direct_answer",
            "WEBCHAT_KNOWLEDGE_REPLY_MODE",
        ),
        (
            "KNOWLEDGE_EMBEDDINGS_ENABLED",
            "false",
            "KNOWLEDGE_EMBEDDINGS_ENABLED",
        ),
        (
            "KNOWLEDGE_EMBEDDING_PROVIDER",
            "deterministic_hash",
            "real embedding provider",
        ),
        ("WEBCHAT_WS_BROKER", "memory", "WEBCHAT_WS_BROKER=memory"),
        ("WHATSAPP_DISPATCH_MODE", "bad-mode", "WHATSAPP_DISPATCH_MODE"),
    ],
)
def test_production_web_settings_reject_unsafe_contract(
    key: str,
    value: str,
    expected_message: str,
):
    extra = {"WEBCHAT_WS_ENABLED": "true"} if key == "WEBCHAT_WS_BROKER" else {}
    with pytest.MonkeyPatch.context() as monkeypatch:
        _frontend_exists(monkeypatch)
        with patched_env(production_env(**extra, **{key: value})):
            with pytest.raises(RuntimeError) as exc:
                Settings()
    assert expected_message in str(exc.value)


@pytest.mark.parametrize(
    "role",
    [
        "migration",
        "worker-outbound",
        "worker-background",
        "worker-handoff-snapshot",
    ],
)
def test_non_http_production_roles_do_not_require_web_or_ai_secrets(
    role: str,
    tmp_path,
):
    env = production_env(
        NEXUS_PROCESS_ROLE=role,
        STORAGE_BACKEND="local",
        UPLOAD_ROOT=str(tmp_path / role),
        WEBCHAT_AI_ENABLED="false",
        WEBCHAT_AI_AUTO_REPLY_MODE="off",
        PROVIDER_RUNTIME_ENABLED="false",
        PRIVATE_AI_RUNTIME_ENABLED="false",
        KNOWLEDGE_EMBEDDINGS_ENABLED="false",
        METRICS_ENABLED="false",
    )
    for key in (
        "SECRET_KEY",
        "METRICS_TOKEN",
        "ALLOWED_ORIGINS",
        "WEBCHAT_ALLOWED_ORIGINS",
        "KNOWLEDGE_EMBEDDING_API_KEY",
        "KNOWLEDGE_EMBEDDING_API_KEY_FILE",
    ):
        env.pop(key, None)

    with pytest.MonkeyPatch.context() as monkeypatch:
        _clear_web_and_ai_secrets(monkeypatch)
        with patched_env(env):
            settings = Settings()

    assert settings.process_role == role
    assert settings.is_http_process is False
    assert settings.is_postgres is True
    assert settings.jwt_secret_key is None
    assert settings.knowledge_embeddings_enabled is False


def test_webchat_ai_worker_requires_real_embedding_only_when_ai_is_enabled(tmp_path):
    disabled_env = production_env(
        NEXUS_PROCESS_ROLE="worker-webchat-ai",
        STORAGE_BACKEND="local",
        UPLOAD_ROOT=str(tmp_path / "disabled"),
        WEBCHAT_AI_ENABLED="false",
        WEBCHAT_AI_AUTO_REPLY_MODE="off",
        PROVIDER_RUNTIME_ENABLED="false",
        PRIVATE_AI_RUNTIME_ENABLED="false",
        KNOWLEDGE_EMBEDDINGS_ENABLED="false",
        METRICS_ENABLED="false",
    )
    for key in (
        "SECRET_KEY",
        "METRICS_TOKEN",
        "ALLOWED_ORIGINS",
        "WEBCHAT_ALLOWED_ORIGINS",
        "KNOWLEDGE_EMBEDDING_API_KEY",
        "KNOWLEDGE_EMBEDDING_API_KEY_FILE",
    ):
        disabled_env.pop(key, None)

    with pytest.MonkeyPatch.context() as monkeypatch:
        _clear_web_and_ai_secrets(monkeypatch)
        with patched_env(disabled_env):
            disabled = Settings()
    assert disabled.knowledge_embeddings_enabled is False

    enabled_env = {
        **disabled_env,
        "UPLOAD_ROOT": str(tmp_path / "enabled"),
        "WEBCHAT_AI_ENABLED": "true",
        "WEBCHAT_AI_AUTO_REPLY_MODE": "runtime",
    }
    with pytest.MonkeyPatch.context() as monkeypatch:
        _clear_web_and_ai_secrets(monkeypatch)
        with patched_env(enabled_env):
            with pytest.raises(
                RuntimeError,
                match="KNOWLEDGE_EMBEDDINGS_ENABLED=true",
            ):
                Settings()


def test_unsupported_process_role_fails_closed(tmp_path):
    env = production_env(
        NEXUS_PROCESS_ROLE="unknown-worker",
        STORAGE_BACKEND="local",
        UPLOAD_ROOT=str(tmp_path / "unknown"),
        WEBCHAT_AI_ENABLED="false",
        WEBCHAT_AI_AUTO_REPLY_MODE="off",
        KNOWLEDGE_EMBEDDINGS_ENABLED="false",
    )
    with patched_env(env):
        with pytest.raises(RuntimeError, match="NEXUS_PROCESS_ROLE"):
            Settings()


def test_native_whatsapp_dispatch_mode_requires_explicit_enable_and_token(monkeypatch):
    _frontend_exists(monkeypatch)

    with pytest.MonkeyPatch.context() as patch:
        _frontend_exists(patch)
        with patched_env(production_env(WHATSAPP_DISPATCH_MODE="native_sidecar")):
            with pytest.raises(RuntimeError, match="WHATSAPP_NATIVE_ENABLED=true"):
                Settings()

    with pytest.MonkeyPatch.context() as patch:
        _frontend_exists(patch)
        with patched_env(
            production_env(
                WHATSAPP_DISPATCH_MODE="native_sidecar",
                WHATSAPP_NATIVE_ENABLED="true",
            )
        ):
            with pytest.raises(RuntimeError, match="WHATSAPP_SIDECAR_TOKEN"):
                Settings()

    with pytest.MonkeyPatch.context() as patch:
        _frontend_exists(patch)
        with patched_env(
            production_env(
                WHATSAPP_DISPATCH_MODE="native_sidecar",
                WHATSAPP_NATIVE_ENABLED="true",
                WHATSAPP_SIDECAR_TOKEN="sidecar-token",
                WHATSAPP_SIDECAR_URL="http://whatsapp-sidecar:18793",
            )
        ):
            with pytest.raises(RuntimeError, match="WHATSAPP_CONNECTOR_KEY"):
                Settings()

    with pytest.MonkeyPatch.context() as patch:
        _frontend_exists(patch)
        with patched_env(
            production_env(
                WHATSAPP_DISPATCH_MODE="native_sidecar",
                WHATSAPP_NATIVE_ENABLED="true",
                WHATSAPP_SIDECAR_TOKEN="sidecar-token",
                WHATSAPP_CONNECTOR_KEY="connector-key",
                WHATSAPP_SIDECAR_URL="http://whatsapp-sidecar:18793",
            )
        ):
            with pytest.raises(RuntimeError, match="WHATSAPP_CONNECTOR_HMAC_SECRET"):
                Settings()

    with pytest.MonkeyPatch.context() as patch:
        _frontend_exists(patch)
        with patched_env(
            production_env(
                WHATSAPP_DISPATCH_MODE="native_sidecar",
                WHATSAPP_NATIVE_ENABLED="true",
                WHATSAPP_SIDECAR_TOKEN="sidecar-token",
                WHATSAPP_CONNECTOR_KEY="connector-key",
                WHATSAPP_CONNECTOR_HMAC_SECRET="connector-hmac-secret",
                WHATSAPP_SIDECAR_URL="http://whatsapp-sidecar:18793",
            )
        ):
            settings = Settings()
    assert settings.whatsapp_dispatch_mode == "native_sidecar"
    assert settings.whatsapp_native_enabled is True
