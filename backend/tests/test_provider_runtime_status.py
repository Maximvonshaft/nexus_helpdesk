from __future__ import annotations

from app.api.admin_provider_runtime import _sanitize_provider_runtime_snapshot
import app.services.provider_runtime_status as provider_runtime_status_module
from app.services.provider_runtime_status import get_human_webcall_runtime_status, get_provider_runtime_status
from app.services.webchat_runtime_config import get_webchat_runtime_settings


_ENV_KEYS = [
    "APP_ENV",
    "WEBCHAT_AI_ENABLED",
    "WEBCHAT_AI_HISTORY_TURNS",
    "PROVIDER_RUNTIME_PRIMARY_PROVIDER",
    "PRIVATE_AI_RUNTIME_ENABLED",
    "PRIVATE_AI_RUNTIME_BASE_URL",
    "PRIVATE_AI_RUNTIME_TOKEN",
    "PRIVATE_AI_RUNTIME_TOKEN_FILE",
    "PRIVATE_AI_RUNTIME_DIRECT_PATH",
    "PRIVATE_AI_RUNTIME_RAG_PATH",
    "PRIVATE_AI_RUNTIME_CHAT_MODE",
    "PRIVATE_AI_RUNTIME_REQUEST_SHAPE",
    "PRIVATE_AI_RUNTIME_DIRECT_MODEL",
    "PRIVATE_AI_RUNTIME_RAG_MODEL",
    "PRIVATE_AI_RUNTIME_RAG_BASE_URL",
    "PRIVATE_AI_RUNTIME_ALLOW_SHARED_RAG_MODEL",
    "PRIVATE_AI_RUNTIME_TIMEOUT_SECONDS",
]


def _clear_env(monkeypatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    get_webchat_runtime_settings.cache_clear()


def test_provider_runtime_status_default_private_runtime_not_configured(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")

    status = get_provider_runtime_status()

    assert status["ok"] is False
    assert status["status"] == "warning"
    assert status["configured_provider"] == "private_ai_runtime"
    assert "private_ai_runtime is disabled" in status["warnings"]
    assert "private_ai_runtime base URL is missing" in status["warnings"]
    assert status["boundary"] == {
        "secret_values_exposed": False,
        "external_network_call": False,
        "customer_message_sent": False,
    }


def test_provider_runtime_status_private_ai_runtime_ready_without_secret_echo(monkeypatch):
    _clear_env(monkeypatch)
    secret = "private-ai-runtime-secret"
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", "private_ai_runtime")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN", secret)

    status = get_provider_runtime_status()

    assert status["ok"] is True
    assert status["status"] == "ready"
    assert status["fallback_provider"] is None
    private_ai = next(item for item in status["providers"] if item["name"] == "private_ai_runtime")
    assert private_ai["selected"] is True
    assert private_ai["feature_enabled"] is True
    assert private_ai["configured"] is True
    assert private_ai["runtime"] == "server_side_ai_runtime"
    assert private_ai["capabilities"]["webchat_runtime_reply"] is True
    assert private_ai["diagnostics"]["direct_path"] == "/api/chat"
    assert private_ai["diagnostics"]["request_shape"] == "ollama_chat"
    assert secret not in str(status)


def test_provider_runtime_status_production_requires_token_file(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", "private_ai_runtime")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN", "inline-token")

    status = get_provider_runtime_status()

    assert status["ok"] is False
    assert "private_ai_runtime token file is missing" in status["warnings"]
    assert "private_ai_runtime inline token is forbidden in production" in status["warnings"]
    assert "inline-token" not in str(status)


def test_provider_runtime_status_warns_on_endpoint_shape_mismatch(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", "private_ai_runtime")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN", "inline-token")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_DIRECT_PATH", "/chat/direct")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "ollama_chat")

    status = get_provider_runtime_status()

    assert status["ok"] is False
    assert "private_ai_runtime endpoint and request shape are incompatible" in status["warnings"]


def test_provider_runtime_status_warns_when_production_rag_model_is_not_isolated(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", "private_ai_runtime")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", "/run/nexus/ai_runtime_token")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_CHAT_MODE", "rag")

    status = get_provider_runtime_status()

    assert status["ok"] is False
    assert "private_ai_runtime RAG model requires an isolated runtime" in status["warnings"]
    private_ai = next(item for item in status["providers"] if item["name"] == "private_ai_runtime")
    assert private_ai["diagnostics"]["rag_runtime_isolated"] is False


def test_provider_runtime_status_accepts_isolated_production_rag_runtime(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", "private_ai_runtime")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_RAG_BASE_URL", "http://ai-runtime-rag.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", "/run/nexus/ai_runtime_token")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_CHAT_MODE", "rag")

    status = get_provider_runtime_status()

    assert "private_ai_runtime RAG model requires an isolated runtime" not in status["warnings"]
    private_ai = next(item for item in status["providers"] if item["name"] == "private_ai_runtime")
    assert private_ai["diagnostics"]["rag_runtime_isolated"] is True


def test_provider_runtime_status_masks_runtime_configuration_exception(monkeypatch):
    sensitive_exception = "stack trace includes token=TOP-SECRET"

    def raise_runtime_error():
        raise RuntimeError(sensitive_exception)

    monkeypatch.setattr(provider_runtime_status_module, "get_webchat_runtime_settings", raise_runtime_error)

    status = get_provider_runtime_status()

    assert status["status"] == "misconfigured"
    assert status["config_error"] == "provider_runtime_configuration_invalid"
    assert sensitive_exception not in str(status)


def test_human_webcall_status_masks_runtime_configuration_exception(monkeypatch):
    sensitive_exception = "voice provider secret and stack trace"

    def raise_runtime_error():
        raise RuntimeError(sensitive_exception)

    monkeypatch.setattr(provider_runtime_status_module, "load_webchat_voice_runtime_config", raise_runtime_error)

    status = get_human_webcall_runtime_status()

    assert status["readiness_verdict"] == "disabled"
    assert status["warnings"] == ["human_webcall runtime configuration invalid"]
    assert sensitive_exception not in str(status)


def test_human_webcall_status_unavailable_survives_admin_sanitization(monkeypatch):
    sensitive_exception = "database failure includes private status details"

    class VoiceConfig:
        enabled = True
        provider = "livekit"
        recording_enabled = False
        transcription_enabled = False

    def raise_status_error(*args, **kwargs):
        raise TypeError(sensitive_exception)

    monkeypatch.setattr(provider_runtime_status_module, "load_webchat_voice_runtime_config", lambda: VoiceConfig())
    monkeypatch.setattr(provider_runtime_status_module, "_human_webcall_count", raise_status_error)

    status = get_human_webcall_runtime_status(object())
    sanitized = _sanitize_provider_runtime_snapshot({"human_webcall": status})

    assert status["readiness_verdict"] == "warning"
    assert status["warnings"] == ["human_webcall status unavailable"]
    assert sanitized["human_webcall"]["warnings"] == ["human_webcall status unavailable"]
    assert sensitive_exception not in str(status)
    assert sensitive_exception not in str(sanitized)
