from __future__ import annotations

from app.services.provider_runtime_status import get_provider_runtime_status
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
    "PRIVATE_AI_RUNTIME_CAPABILITIES_PATH",
    "PRIVATE_AI_RUNTIME_CHAT_MODE",
    "PRIVATE_AI_RUNTIME_REQUEST_SHAPE",
    "PRIVATE_AI_RUNTIME_GENERATION_MODEL",
    "PRIVATE_AI_RUNTIME_DIRECT_MODEL",
    "PRIVATE_AI_RUNTIME_RAG_MODEL",
    "PRIVATE_AI_RUNTIME_RAG_BASE_URL",
    "PRIVATE_AI_RUNTIME_TIMEOUT_SECONDS",
    "PRIVATE_AI_RUNTIME_CAPABILITY_TIMEOUT_SECONDS",
    "PRIVATE_AI_RUNTIME_EXPECTED_RUNTIME_ID",
    "PRIVATE_AI_RUNTIME_EXPECTED_RUNTIME_VERSION",
    "PRIVATE_AI_RUNTIME_EXPECTED_GENERATION_MODEL",
    "PRIVATE_AI_RUNTIME_EXPECTED_GENERATION_PATH",
    "PRIVATE_AI_RUNTIME_EXPECTED_REQUEST_CONTRACT",
    "PRIVATE_AI_RUNTIME_EXPECTED_RESPONSE_CONTRACT",
    "PRIVATE_AI_RUNTIME_EXPECTED_RETRIEVAL_BACKEND",
    "PRIVATE_AI_RUNTIME_EXPECTED_EMBEDDING_MODEL",
    "PRIVATE_AI_RUNTIME_EXPECTED_EMBEDDING_DIMENSION",
    "PRIVATE_AI_RUNTIME_EXPECTED_RERANKER_MODEL",
    "PRIVATE_AI_RUNTIME_EXPECTED_COLLECTION_ALIAS",
]


def _clear_env(monkeypatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    get_webchat_runtime_settings.cache_clear()


def _configure_expectations(monkeypatch) -> None:
    values = {
        "PRIVATE_AI_RUNTIME_EXPECTED_RUNTIME_ID": "nexus-private-ai-runtime",
        "PRIVATE_AI_RUNTIME_EXPECTED_RUNTIME_VERSION": "2026.07.12.1",
        "PRIVATE_AI_RUNTIME_EXPECTED_GENERATION_MODEL": "nexus-gemma4-e4b:latest",
        "PRIVATE_AI_RUNTIME_EXPECTED_GENERATION_PATH": "/api/chat",
        "PRIVATE_AI_RUNTIME_EXPECTED_REQUEST_CONTRACT": "ollama.chat.v1",
        "PRIVATE_AI_RUNTIME_EXPECTED_RESPONSE_CONTRACT": (
            "nexus_webchat_runtime_reply_v1"
        ),
        "PRIVATE_AI_RUNTIME_EXPECTED_RETRIEVAL_BACKEND": "qdrant",
        "PRIVATE_AI_RUNTIME_EXPECTED_EMBEDDING_MODEL": "qwen3-embedding",
        "PRIVATE_AI_RUNTIME_EXPECTED_EMBEDDING_DIMENSION": "1024",
        "PRIVATE_AI_RUNTIME_EXPECTED_RERANKER_MODEL": "qwen3-reranker",
        "PRIVATE_AI_RUNTIME_EXPECTED_COLLECTION_ALIAS": "nexus-knowledge-active",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _configure_ready_runtime(monkeypatch, *, app_env: str = "production") -> None:
    _configure_expectations(monkeypatch)
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv(
        "PROVIDER_RUNTIME_PRIMARY_PROVIDER",
        "private_ai_runtime",
    )
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv(
        "PRIVATE_AI_RUNTIME_BASE_URL",
        "http://ai-runtime.internal:18081",
    )
    monkeypatch.setenv(
        "PRIVATE_AI_RUNTIME_TOKEN_FILE",
        "/run/nexus/ai_runtime_token",
    )
    monkeypatch.setenv(
        "PRIVATE_AI_RUNTIME_GENERATION_MODEL",
        "nexus-gemma4-e4b:latest",
    )


def test_provider_runtime_status_default_private_runtime_not_configured(
    monkeypatch,
):
    _clear_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")

    status = get_provider_runtime_status()

    assert status["ok"] is False
    assert status["status"] == "warning"
    assert status["configured_provider"] == "private_ai_runtime"
    assert "private_ai_runtime is disabled" in status["warnings"]
    assert "private_ai_runtime base URL is missing" in status["warnings"]
    assert (
        "private_ai_runtime capability expectation invalid: "
        "capability_expectation_missing"
        in status["warnings"]
    )
    assert status["boundary"] == {
        "secret_values_exposed": False,
        "external_network_call": False,
        "customer_message_sent": False,
        "internal_endpoint_exposed": False,
    }


def test_provider_runtime_status_private_ai_runtime_ready_without_secret_echo(
    monkeypatch,
):
    _clear_env(monkeypatch)
    _configure_ready_runtime(monkeypatch, app_env="development")
    secret = "private-ai-runtime-secret"
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN", secret)

    status = get_provider_runtime_status()

    assert status["ok"] is True
    assert status["status"] == "ready"
    assert status["fallback_provider"] is None
    private_ai = next(
        item
        for item in status["providers"]
        if item["name"] == "private_ai_runtime"
    )
    assert private_ai["selected"] is True
    assert private_ai["feature_enabled"] is True
    assert private_ai["configured"] is True
    assert private_ai["runtime"] == "server_side_ai_runtime"
    assert private_ai["capabilities"]["webchat_runtime_reply"] is True
    assert private_ai["diagnostics"]["direct_path"] == "/api/chat"
    assert private_ai["diagnostics"]["capabilities_path"] == "/v1/capabilities"
    assert private_ai["diagnostics"]["request_shape"] == "ollama_chat"
    assert (
        private_ai["diagnostics"]["generation_model"]
        == "nexus-gemma4-e4b:latest"
    )
    assert (
        private_ai["diagnostics"]["capability_expectation"]["status"]
        == "ready"
    )
    assert (
        private_ai["diagnostics"]["local_generation_configuration"]["status"]
        == "ready"
    )
    assert secret not in str(status)
    assert "ai-runtime.internal" not in str(status)
    assert "/run/nexus/ai_runtime_token" not in str(status)


def test_provider_runtime_status_inline_token_cannot_satisfy_capability_gate(
    monkeypatch,
):
    _clear_env(monkeypatch)
    _configure_expectations(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv(
        "PROVIDER_RUNTIME_PRIMARY_PROVIDER",
        "private_ai_runtime",
    )
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv(
        "PRIVATE_AI_RUNTIME_BASE_URL",
        "http://ai-runtime.internal:18081",
    )
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN", "inline-token")

    status = get_provider_runtime_status()

    assert status["ok"] is False
    assert status["providers"][0]["configured"] is False
    assert "private_ai_runtime token file is missing" in status["warnings"]
    assert (
        "private_ai_runtime inline token cannot satisfy capability verification"
        in status["warnings"]
    )
    assert "inline-token" not in str(status)


def test_provider_runtime_status_production_requires_token_file(monkeypatch):
    _clear_env(monkeypatch)
    _configure_expectations(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(
        "PROVIDER_RUNTIME_PRIMARY_PROVIDER",
        "private_ai_runtime",
    )
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv(
        "PRIVATE_AI_RUNTIME_BASE_URL",
        "http://ai-runtime.internal:18081",
    )
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN", "inline-token")

    status = get_provider_runtime_status()

    assert status["ok"] is False
    assert "private_ai_runtime token file is missing" in status["warnings"]
    assert (
        "private_ai_runtime inline token is forbidden in production"
        in status["warnings"]
    )
    assert "inline-token" not in str(status)


def test_provider_runtime_status_warns_on_endpoint_shape_mismatch(monkeypatch):
    _clear_env(monkeypatch)
    _configure_ready_runtime(monkeypatch, app_env="development")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_DIRECT_PATH", "/chat/direct")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "ollama_chat")

    status = get_provider_runtime_status()

    assert status["ok"] is False
    assert (
        "private_ai_runtime endpoint and request shape are incompatible"
        in status["warnings"]
    )
    private_ai = status["providers"][0]
    assert private_ai["configured"] is False


def test_provider_runtime_status_rejects_local_generation_model_drift(
    monkeypatch,
):
    _clear_env(monkeypatch)
    _configure_ready_runtime(monkeypatch)
    monkeypatch.setenv(
        "PRIVATE_AI_RUNTIME_GENERATION_MODEL",
        "other-generation-model",
    )

    status = get_provider_runtime_status()

    assert status["ok"] is False
    private_ai = status["providers"][0]
    assert private_ai["configured"] is False
    local_status = private_ai["diagnostics"][
        "local_generation_configuration"
    ]
    assert local_status["status"] == "not_ready"
    assert local_status["reason_codes"] == [
        "capability_generation_model_mismatch"
    ]
    assert (
        "private_ai_runtime local generation configuration invalid: "
        "capability_generation_model_mismatch"
        in status["warnings"]
    )


def test_provider_runtime_status_rejects_stale_legacy_qwen_model_configuration(
    monkeypatch,
):
    _clear_env(monkeypatch)
    _configure_ready_runtime(monkeypatch)
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_DIRECT_MODEL", "qwen2.5:3b")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_RAG_MODEL", "qwen3:4b")

    status = get_provider_runtime_status()

    assert status["ok"] is False
    assert (
        "private_ai_runtime legacy model configuration conflicts with generation model"
        in status["warnings"]
    )
    private_ai = status["providers"][0]
    assert private_ai["configured"] is False
    assert (
        private_ai["diagnostics"]["legacy_model_configuration_valid"]
        is False
    )
    assert "direct_model" not in private_ai["diagnostics"]
    assert "rag_model" not in private_ai["diagnostics"]


def test_provider_runtime_status_treats_retrieval_as_independent_capability(
    monkeypatch,
):
    _clear_env(monkeypatch)
    _configure_ready_runtime(monkeypatch)
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_CHAT_MODE", "rag")

    status = get_provider_runtime_status()

    assert status["ok"] is True
    private_ai = status["providers"][0]
    assert private_ai["configured"] is True
    assert (
        private_ai["diagnostics"]["capability_expectation"]["expected"][
            "retrieval"
        ]
        == {
            "backend": "qdrant",
            "embedding_model": "qwen3-embedding",
            "embedding_dimension": 1024,
            "reranker_model": "qwen3-reranker",
            "collection_alias": "nexus-knowledge-active",
        }
    )
