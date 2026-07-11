from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from infra.private_ai_runtime.capability_api import create_capability_router


def valid_manifest() -> dict:
    return {
        "schema": "nexus.ai_runtime.capabilities.v1",
        "runtime": {"id": "nexus-private-ai-runtime", "version": "2026.07.12.1"},
        "readiness": {"state": "ready", "reason_codes": []},
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
            "stt": {"enabled": True, "model": "faster-whisper-large-v3"},
            "tts": {"enabled": True, "model": "kokoro"},
            "live_voice": True,
        },
    }


def client_for_files(manifest_file: Path, token_file: Path) -> TestClient:
    app = FastAPI()
    app.include_router(create_capability_router(manifest_file=manifest_file, token_file=token_file))
    return TestClient(app)


def write_valid_files(tmp_path: Path) -> tuple[Path, Path]:
    manifest_file = tmp_path / "capabilities.json"
    manifest_file.write_text(json.dumps(valid_manifest()), encoding="utf-8")
    token_file = tmp_path / "runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    return manifest_file, token_file


def test_capability_endpoint_requires_bearer_token(tmp_path: Path) -> None:
    manifest_file, token_file = write_valid_files(tmp_path)
    client = client_for_files(manifest_file, token_file)

    response = client.get("/v1/capabilities")

    assert response.status_code == 401
    assert response.json() == {"detail": {"reason_code": "capability_unauthorized"}}
    assert response.headers["cache-control"] == "no-store"


def test_capability_endpoint_rejects_wrong_token_without_echo(tmp_path: Path) -> None:
    manifest_file, token_file = write_valid_files(tmp_path)
    client = client_for_files(manifest_file, token_file)

    response = client.get(
        "/v1/capabilities",
        headers={"Authorization": "Bearer wrong-secret-token"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": {"reason_code": "capability_unauthorized"}}
    assert "wrong-secret-token" not in response.text
    assert "test-token" not in response.text


def test_capability_endpoint_serves_only_valid_safe_manifest(tmp_path: Path) -> None:
    manifest_file, token_file = write_valid_files(tmp_path)
    client = client_for_files(manifest_file, token_file)

    response = client.get(
        "/v1/capabilities",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert response.json() == valid_manifest()
    assert response.headers["cache-control"] == "no-store"
    rendered = response.text.lower()
    assert "authorization" not in rendered
    assert "test-token" not in rendered
    assert str(manifest_file).lower() not in rendered
    assert str(token_file).lower() not in rendered


def test_capability_endpoint_fails_closed_when_token_file_missing(tmp_path: Path) -> None:
    manifest_file = tmp_path / "capabilities.json"
    manifest_file.write_text(json.dumps(valid_manifest()), encoding="utf-8")
    token_file = tmp_path / "missing-token"
    client = client_for_files(manifest_file, token_file)

    response = client.get(
        "/v1/capabilities",
        headers={"Authorization": "Bearer any-value"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": {"reason_code": "capability_token_unavailable"}}
    assert str(token_file) not in response.text


def test_capability_endpoint_rejects_malformed_manifest_without_detail(tmp_path: Path) -> None:
    manifest_file = tmp_path / "capabilities.json"
    manifest_file.write_text("{", encoding="utf-8")
    token_file = tmp_path / "runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    client = client_for_files(manifest_file, token_file)

    response = client.get(
        "/v1/capabilities",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": {"reason_code": "capability_manifest_unavailable"}}
    assert "json" not in response.text.lower()
    assert str(manifest_file) not in response.text


def test_capability_endpoint_rejects_duplicate_secret_and_oversized_manifest(tmp_path: Path) -> None:
    token_file = tmp_path / "runtime-token"
    token_file.write_text("test-token", encoding="utf-8")

    invalid_payloads = [
        '{"schema":"nexus.ai_runtime.capabilities.v1","schema":"downgraded"}',
        json.dumps({**valid_manifest(), "token": "must-not-leak"}),
        "x" * (32 * 1024 + 1),
    ]

    for index, payload in enumerate(invalid_payloads):
        manifest_file = tmp_path / f"capabilities-{index}.json"
        manifest_file.write_text(payload, encoding="utf-8")
        client = client_for_files(manifest_file, token_file)

        response = client.get(
            "/v1/capabilities",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 503
        assert response.json() == {"detail": {"reason_code": "capability_manifest_unavailable"}}
        assert "must-not-leak" not in response.text
        assert str(manifest_file) not in response.text
