from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.provider_runtime.runtime_capabilities import (
    CAPABILITY_SCHEMA,
    CapabilityExpectationError,
    CapabilityManifestError,
    RuntimeCapabilityExpectations,
    build_capability_url,
    evaluate_capability_manifest,
    load_capability_expectations_from_env,
    parse_capability_manifest,
    probe_private_ai_runtime_capabilities,
)


def valid_manifest() -> dict:
    return {
        "schema": "nexus.ai_runtime.capabilities.v1",
        "runtime": {
            "id": "nexus-private-ai-runtime",
            "version": "2026.07.12.1",
        },
        "readiness": {
            "state": "ready",
            "reason_codes": [],
        },
        "generation": {
            "model": "nexus-gemma4-e4b:latest",
            "structured_output": True,
            "api_path": "/api/chat",
            "request_contract": "ollama.chat.v1",
            "response_contract": "nexus_webchat_runtime_reply_v1",
        },
        "retrieval": {
            "enabled": True,
            "backend": "qdrant",
            "embedding_model": "qwen3-embedding",
            "embedding_dimension": 1024,
            "reranker_enabled": True,
            "reranker_model": "qwen3-reranker",
            "collection_alias": "nexus-knowledge-active",
        },
        "voice": {
            "stt": {
                "enabled": True,
                "model": "faster-whisper-large-v3",
            },
            "tts": {
                "enabled": True,
                "model": "kokoro",
            },
            "live_voice": True,
        },
    }


def expectations(**overrides) -> RuntimeCapabilityExpectations:
    data = {
        "schema": CAPABILITY_SCHEMA,
        "runtime_id": "nexus-private-ai-runtime",
        "runtime_version": "2026.07.12.1",
        "generation_model": "nexus-gemma4-e4b:latest",
        "generation_api_path": "/api/chat",
        "request_contract": "ollama.chat.v1",
        "response_contract": "nexus_webchat_runtime_reply_v1",
        "retrieval_backend": "qdrant",
        "embedding_model": "qwen3-embedding",
        "embedding_dimension": 1024,
        "reranker_model": "qwen3-reranker",
        "collection_alias": "nexus-knowledge-active",
    }
    data.update(overrides)
    return RuntimeCapabilityExpectations(**data)


def test_valid_manifest_matches_exact_expectations() -> None:
    manifest = parse_capability_manifest(json.dumps(valid_manifest()).encode("utf-8"))

    result = evaluate_capability_manifest(manifest, expectations())

    assert result.ready is True
    assert result.reason_codes == ()
    assert result.safe_summary() == {
        "schema": "nexus.ai_runtime.capability_probe.v1",
        "status": "ready",
        "reason_codes": [],
        "runtime": {
            "id": "nexus-private-ai-runtime",
            "version": "2026.07.12.1",
        },
        "generation": {
            "available": True,
            "model": "nexus-gemma4-e4b:latest",
            "structured_output": True,
            "api_path": "/api/chat",
            "request_contract": "ollama.chat.v1",
            "response_contract": "nexus_webchat_runtime_reply_v1",
        },
        "retrieval": {
            "available": True,
            "backend": "qdrant",
            "embedding_model": "qwen3-embedding",
            "embedding_dimension": 1024,
            "reranker_available": True,
            "reranker_model": "qwen3-reranker",
            "collection_alias": "nexus-knowledge-active",
        },
        "voice": {
            "stt_available": True,
            "stt_model": "faster-whisper-large-v3",
            "tts_available": True,
            "tts_model": "kokoro",
            "live_voice": True,
        },
        "boundary": {
            "secret_values_exposed": False,
            "internal_endpoint_exposed": False,
            "raw_manifest_exposed": False,
        },
    }


def test_duplicate_json_key_is_rejected() -> None:
    raw = b'{"schema":"nexus.ai_runtime.capabilities.v1","schema":"downgraded"}'

    with pytest.raises(CapabilityManifestError) as exc_info:
        parse_capability_manifest(raw)

    assert exc_info.value.reason_code == "capability_payload_malformed"
    assert "schema" not in str(exc_info.value)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.update({"unexpected": True}),
        lambda payload: payload["runtime"].update({"hostname": "runtime.internal"}),
        lambda payload: payload["generation"].update({"token": "must-not-appear"}),
        lambda payload: payload["retrieval"].update({"endpoint_url": "http://internal"}),
        lambda payload: payload["voice"]["stt"].update({"credential": "must-not-appear"}),
    ],
)
def test_unknown_or_secret_like_fields_are_rejected(mutator) -> None:
    payload = valid_manifest()
    mutator(payload)

    with pytest.raises(CapabilityManifestError) as exc_info:
        parse_capability_manifest(json.dumps(payload))

    assert exc_info.value.reason_code == "capability_payload_malformed"
    assert "must-not-appear" not in str(exc_info.value)
    assert "runtime.internal" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("field_path", "value"),
    [
        (("runtime", "version"), ""),
        (("generation", "api_path"), "https://runtime.internal/api/chat"),
        (("generation", "api_path"), "/api/chat?debug=true"),
        (("retrieval", "embedding_dimension"), True),
        (("retrieval", "embedding_dimension"), "1024"),
        (("retrieval", "embedding_dimension"), 0),
        (("retrieval", "embedding_dimension"), 65537),
    ],
)
def test_invalid_manifest_values_are_rejected(field_path, value) -> None:
    payload = valid_manifest()
    parent = payload
    for key in field_path[:-1]:
        parent = parent[key]
    parent[field_path[-1]] = value

    with pytest.raises(CapabilityManifestError) as exc_info:
        parse_capability_manifest(json.dumps(payload))

    assert exc_info.value.reason_code == "capability_payload_malformed"


def test_not_ready_manifest_requires_bounded_reason_code() -> None:
    payload = valid_manifest()
    payload["readiness"] = {
        "state": "not_ready",
        "reason_codes": ["runtime_model_loading"],
    }

    manifest = parse_capability_manifest(json.dumps(payload))
    result = evaluate_capability_manifest(manifest, expectations())

    assert result.ready is False
    assert result.reason_codes == ("capability_runtime_not_ready",)
    assert result.safe_summary()["status"] == "not_ready"


@pytest.mark.parametrize(
    ("override", "reason_code"),
    [
        ({"schema": "nexus.ai_runtime.capabilities.v0"}, "capability_schema_unsupported"),
        ({"runtime_id": "other-runtime"}, "capability_runtime_identity_mismatch"),
        ({"runtime_version": "2026.07.12.2"}, "capability_runtime_version_mismatch"),
        ({"generation_model": "other-model"}, "capability_generation_model_mismatch"),
        ({"generation_api_path": "/v2/generate"}, "capability_generation_contract_mismatch"),
        ({"request_contract": "other.request.v1"}, "capability_generation_contract_mismatch"),
        ({"response_contract": "other.response.v1"}, "capability_generation_contract_mismatch"),
        ({"retrieval_backend": "other-store"}, "capability_retrieval_backend_mismatch"),
        ({"embedding_model": "other-embedding"}, "capability_embedding_model_mismatch"),
        ({"embedding_dimension": 768}, "capability_embedding_dimension_mismatch"),
        ({"reranker_model": "other-reranker"}, "capability_reranker_model_mismatch"),
        ({"collection_alias": "other-alias"}, "capability_collection_alias_mismatch"),
    ],
)
def test_exact_identity_mismatch_fails_closed(override, reason_code) -> None:
    manifest = parse_capability_manifest(json.dumps(valid_manifest()))

    result = evaluate_capability_manifest(manifest, expectations(**override))

    assert result.ready is False
    assert result.reason_codes == (reason_code,)
    assert result.safe_summary()["status"] == "not_ready"


def test_missing_reranker_fails_closed() -> None:
    payload = valid_manifest()
    payload["retrieval"]["reranker_enabled"] = False
    payload["retrieval"]["reranker_model"] = None

    result = evaluate_capability_manifest(
        parse_capability_manifest(json.dumps(payload)),
        expectations(),
    )

    assert result.ready is False
    assert result.reason_codes == ("capability_reranker_missing",)


def test_voice_capabilities_are_independent_from_generation_and_retrieval() -> None:
    payload = valid_manifest()
    payload["voice"] = {
        "stt": {"enabled": False, "model": None},
        "tts": {"enabled": False, "model": None},
        "live_voice": False,
    }

    result = evaluate_capability_manifest(
        parse_capability_manifest(json.dumps(payload)),
        expectations(),
    )

    assert result.ready is True
    assert result.safe_summary()["generation"]["available"] is True
    assert result.safe_summary()["retrieval"]["available"] is True
    assert result.safe_summary()["voice"] == {
        "stt_available": False,
        "stt_model": None,
        "tts_available": False,
        "tts_model": None,
        "live_voice": False,
    }


def test_expectations_load_exact_required_environment(monkeypatch) -> None:
    values = {
        "PRIVATE_AI_RUNTIME_EXPECTED_RUNTIME_ID": "nexus-private-ai-runtime",
        "PRIVATE_AI_RUNTIME_EXPECTED_RUNTIME_VERSION": "2026.07.12.1",
        "PRIVATE_AI_RUNTIME_EXPECTED_GENERATION_MODEL": "nexus-gemma4-e4b:latest",
        "PRIVATE_AI_RUNTIME_EXPECTED_GENERATION_PATH": "/api/chat",
        "PRIVATE_AI_RUNTIME_EXPECTED_REQUEST_CONTRACT": "ollama.chat.v1",
        "PRIVATE_AI_RUNTIME_EXPECTED_RESPONSE_CONTRACT": "nexus_webchat_runtime_reply_v1",
        "PRIVATE_AI_RUNTIME_EXPECTED_RETRIEVAL_BACKEND": "qdrant",
        "PRIVATE_AI_RUNTIME_EXPECTED_EMBEDDING_MODEL": "qwen3-embedding",
        "PRIVATE_AI_RUNTIME_EXPECTED_EMBEDDING_DIMENSION": "1024",
        "PRIVATE_AI_RUNTIME_EXPECTED_RERANKER_MODEL": "qwen3-reranker",
        "PRIVATE_AI_RUNTIME_EXPECTED_COLLECTION_ALIAS": "nexus-knowledge-active",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    loaded = load_capability_expectations_from_env()

    assert loaded == expectations()


@pytest.mark.parametrize(
    ("name", "value", "reason_code"),
    [
        ("PRIVATE_AI_RUNTIME_EXPECTED_RUNTIME_VERSION", "", "capability_expectation_missing"),
        ("PRIVATE_AI_RUNTIME_EXPECTED_EMBEDDING_DIMENSION", "true", "capability_expectation_invalid"),
        ("PRIVATE_AI_RUNTIME_EXPECTED_GENERATION_PATH", "https://internal/api", "capability_expectation_invalid"),
    ],
)
def test_missing_or_invalid_expectation_fails_closed(monkeypatch, name, value, reason_code) -> None:
    base = {
        "PRIVATE_AI_RUNTIME_EXPECTED_RUNTIME_ID": "nexus-private-ai-runtime",
        "PRIVATE_AI_RUNTIME_EXPECTED_RUNTIME_VERSION": "2026.07.12.1",
        "PRIVATE_AI_RUNTIME_EXPECTED_GENERATION_MODEL": "nexus-gemma4-e4b:latest",
        "PRIVATE_AI_RUNTIME_EXPECTED_GENERATION_PATH": "/api/chat",
        "PRIVATE_AI_RUNTIME_EXPECTED_REQUEST_CONTRACT": "ollama.chat.v1",
        "PRIVATE_AI_RUNTIME_EXPECTED_RESPONSE_CONTRACT": "nexus_webchat_runtime_reply_v1",
        "PRIVATE_AI_RUNTIME_EXPECTED_RETRIEVAL_BACKEND": "qdrant",
        "PRIVATE_AI_RUNTIME_EXPECTED_EMBEDDING_MODEL": "qwen3-embedding",
        "PRIVATE_AI_RUNTIME_EXPECTED_EMBEDDING_DIMENSION": "1024",
        "PRIVATE_AI_RUNTIME_EXPECTED_RERANKER_MODEL": "qwen3-reranker",
        "PRIVATE_AI_RUNTIME_EXPECTED_COLLECTION_ALIAS": "nexus-knowledge-active",
    }
    base[name] = value
    for env_name, env_value in base.items():
        monkeypatch.setenv(env_name, env_value)

    with pytest.raises(CapabilityExpectationError) as exc_info:
        load_capability_expectations_from_env()

    assert exc_info.value.reason_code == reason_code
    assert "internal" not in str(exc_info.value)


@pytest.mark.parametrize(
    "path",
    [
        "https://other.example/v1/capabilities",
        "//other.example/v1/capabilities",
        "/v1/capabilities?token=secret",
        "/v1/capabilities#fragment",
        "v1/capabilities",
    ],
)
def test_capability_url_rejects_authority_query_fragment_and_relative_path(path) -> None:
    with pytest.raises(CapabilityExpectationError) as exc_info:
        build_capability_url("https://runtime.example", path)

    assert exc_info.value.reason_code == "capability_endpoint_invalid"
    assert "secret" not in str(exc_info.value)


def test_capability_url_is_bound_to_runtime_origin() -> None:
    assert (
        build_capability_url("https://runtime.example/base", "/v1/capabilities")
        == "https://runtime.example/v1/capabilities"
    )


class FakeResponse:
    def __init__(self, payload: bytes, *, content_type: str = "application/json", status: int = 200):
        self.payload = payload
        self.headers = {"Content-Type": content_type}
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, limit: int) -> bytes:
        return self.payload[:limit]


class FakeOpener:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        return self.response


def test_probe_reads_token_file_but_returns_only_safe_identity(tmp_path: Path) -> None:
    token_file = tmp_path / "runtime-token"
    token_file.write_text("super-secret-token", encoding="utf-8")
    opener = FakeOpener(FakeResponse(json.dumps(valid_manifest()).encode("utf-8")))

    result = probe_private_ai_runtime_capabilities(
        base_url="https://runtime.example",
        capabilities_path="/v1/capabilities",
        token_file=str(token_file),
        expectations=expectations(),
        timeout_seconds=2,
        opener=opener,
    )

    assert result.ready is True
    assert opener.request.full_url == "https://runtime.example/v1/capabilities"
    assert opener.request.get_header("Authorization") == "Bearer super-secret-token"
    rendered = json.dumps(result.safe_summary())
    assert "super-secret-token" not in rendered
    assert "runtime.example" not in rendered
    assert str(token_file) not in rendered


@pytest.mark.parametrize(
    ("response", "reason_code"),
    [
        (FakeResponse(b"{}", content_type="text/plain"), "capability_content_type_invalid"),
        (FakeResponse(b"{"), "capability_payload_malformed"),
        (FakeResponse(b"x" * (32 * 1024 + 1)), "capability_payload_too_large"),
    ],
)
def test_probe_normalizes_response_failures(tmp_path: Path, response, reason_code) -> None:
    token_file = tmp_path / "runtime-token"
    token_file.write_text("secret", encoding="utf-8")

    result = probe_private_ai_runtime_capabilities(
        base_url="https://runtime.example",
        capabilities_path="/v1/capabilities",
        token_file=str(token_file),
        expectations=expectations(),
        timeout_seconds=2,
        opener=FakeOpener(response),
    )

    assert result.ready is False
    assert result.reason_codes == (reason_code,)
    rendered = json.dumps(result.safe_summary())
    assert "runtime.example" not in rendered
    assert "secret" not in rendered


def test_probe_missing_token_file_fails_closed_without_path_leak(tmp_path: Path) -> None:
    missing = tmp_path / "missing-token"

    result = probe_private_ai_runtime_capabilities(
        base_url="https://runtime.example",
        capabilities_path="/v1/capabilities",
        token_file=str(missing),
        expectations=expectations(),
        timeout_seconds=2,
        opener=FakeOpener(FakeResponse(b"{}")),
    )

    assert result.ready is False
    assert result.reason_codes == ("capability_token_missing",)
    assert str(missing) not in json.dumps(result.safe_summary())
