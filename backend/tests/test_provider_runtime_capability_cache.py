from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.services.provider_runtime.runtime_capabilities import (
    CapabilityProbeResult,
    RuntimeCapabilityExpectations,
)
from app.services.provider_runtime.runtime_capability_cache import (
    CapabilityProbeCache,
    build_capability_cache_key,
)


def expectations() -> RuntimeCapabilityExpectations:
    return RuntimeCapabilityExpectations(
        schema="nexus.ai_runtime.capabilities.v1",
        runtime_id="nexus-private-ai-runtime",
        runtime_version="2026.07.12.1",
        generation_model="nexus-gemma4-e4b:latest",
        generation_api_path="/api/chat",
        request_contract="ollama.chat.v1",
        response_contract="nexus_webchat_runtime_reply_v1",
        retrieval_backend="qdrant",
        embedding_model="qwen3-embedding",
        embedding_dimension=1024,
        reranker_model="qwen3-reranker",
        collection_alias="nexus-knowledge-active",
    )


def test_ready_result_is_reused_only_inside_ready_ttl() -> None:
    now = [100.0]
    cache = CapabilityProbeCache(clock=lambda: now[0])
    calls = []

    def probe() -> CapabilityProbeResult:
        calls.append(now[0])
        return CapabilityProbeResult(ready=True, reason_codes=())

    first = cache.get_or_probe(
        key=("candidate",),
        probe=probe,
        ready_ttl_seconds=10.0,
        not_ready_ttl_seconds=1.0,
    )
    now[0] = 109.9
    second = cache.get_or_probe(
        key=("candidate",),
        probe=probe,
        ready_ttl_seconds=10.0,
        not_ready_ttl_seconds=1.0,
    )
    now[0] = 110.0
    third = cache.get_or_probe(
        key=("candidate",),
        probe=probe,
        ready_ttl_seconds=10.0,
        not_ready_ttl_seconds=1.0,
    )

    assert first is second
    assert third.ready is True
    assert calls == [100.0, 110.0]


def test_not_ready_result_uses_shorter_ttl() -> None:
    now = [50.0]
    cache = CapabilityProbeCache(clock=lambda: now[0])
    calls = []

    def probe() -> CapabilityProbeResult:
        calls.append(now[0])
        return CapabilityProbeResult.not_ready("capability_runtime_not_ready")

    cache.get_or_probe(
        key=("candidate",),
        probe=probe,
        ready_ttl_seconds=10.0,
        not_ready_ttl_seconds=1.0,
    )
    now[0] = 50.9
    cache.get_or_probe(
        key=("candidate",),
        probe=probe,
        ready_ttl_seconds=10.0,
        not_ready_ttl_seconds=1.0,
    )
    now[0] = 51.0
    cache.get_or_probe(
        key=("candidate",),
        probe=probe,
        ready_ttl_seconds=10.0,
        not_ready_ttl_seconds=1.0,
    )

    assert calls == [50.0, 51.0]


def test_concurrent_miss_runs_probe_once() -> None:
    cache = CapabilityProbeCache()
    calls = []

    def probe() -> CapabilityProbeResult:
        calls.append(True)
        time.sleep(0.03)
        return CapabilityProbeResult(ready=True, reason_codes=())

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda _: cache.get_or_probe(
                    key=("candidate",),
                    probe=probe,
                    ready_ttl_seconds=10.0,
                    not_ready_ttl_seconds=1.0,
                ),
                range(8),
            )
        )

    assert len(calls) == 1
    assert all(result.ready for result in results)


def test_cache_key_changes_with_exact_expectations_and_token_file_fingerprint(
    tmp_path: Path,
) -> None:
    token_file = tmp_path / "runtime-token"
    token_file.write_text("first-secret", encoding="utf-8")
    first = build_capability_cache_key(
        base_url="https://runtime.example",
        capabilities_path="/v1/capabilities",
        token_file=str(token_file),
        expectations=expectations(),
    )

    token_file.write_text(
        "second-secret-with-different-size",
        encoding="utf-8",
    )
    os.utime(token_file, None)
    second = build_capability_cache_key(
        base_url="https://runtime.example",
        capabilities_path="/v1/capabilities",
        token_file=str(token_file),
        expectations=expectations(),
    )
    changed_expectations = RuntimeCapabilityExpectations(
        **{
            **expectations().__dict__,
            "runtime_version": "2026.07.12.2",
        }
    )
    third = build_capability_cache_key(
        base_url="https://runtime.example",
        capabilities_path="/v1/capabilities",
        token_file=str(token_file),
        expectations=changed_expectations,
    )

    assert first != second
    assert second != third
    rendered = repr((first, second, third))
    assert "first-secret" not in rendered
    assert "second-secret" not in rendered


def test_clear_invalidates_cached_result() -> None:
    cache = CapabilityProbeCache()
    calls = []

    def probe() -> CapabilityProbeResult:
        calls.append(True)
        return CapabilityProbeResult(ready=True, reason_codes=())

    cache.get_or_probe(
        key=("candidate",),
        probe=probe,
        ready_ttl_seconds=10.0,
        not_ready_ttl_seconds=1.0,
    )
    cache.clear()
    cache.get_or_probe(
        key=("candidate",),
        probe=probe,
        ready_ttl_seconds=10.0,
        not_ready_ttl_seconds=1.0,
    )

    assert len(calls) == 2
