from __future__ import annotations

import os
from typing import Any, Callable

from .provider_runtime.runtime_capabilities import (
    CapabilityExpectationError,
    CapabilityProbeResult,
    RuntimeCapabilityExpectations,
    load_capability_expectations_from_env,
    probe_private_ai_runtime_capabilities,
)

CAPABILITY_EXPECTATION_SCHEMA = "nexus.ai_runtime.capability_expectation.v1"
LOCAL_GENERATION_CONFIGURATION_SCHEMA = (
    "nexus.ai_runtime.local_generation_configuration.v1"
)
_REQUEST_CONTRACT_BY_SHAPE = {
    "ollama_chat": "ollama.chat.v1",
    "messages": "openai.chat.v1",
    "question": "nexus.question.v1",
    "system_input": "nexus.system_input.v1",
}


def _expectation_summary(
    expectations: RuntimeCapabilityExpectations,
) -> dict[str, Any]:
    return {
        "capability_schema": expectations.schema,
        "runtime": {
            "id": expectations.runtime_id,
            "version": expectations.runtime_version,
        },
        "generation": {
            "model": expectations.generation_model,
            "api_path": expectations.generation_api_path,
            "request_contract": expectations.request_contract,
            "response_contract": expectations.response_contract,
        },
        "retrieval": {
            "backend": expectations.retrieval_backend,
            "embedding_model": expectations.embedding_model,
            "embedding_dimension": expectations.embedding_dimension,
            "reranker_model": expectations.reranker_model,
            "collection_alias": expectations.collection_alias,
        },
    }


def get_provider_runtime_capability_expectation_status() -> dict[str, Any]:
    try:
        expectations = load_capability_expectations_from_env()
    except CapabilityExpectationError as exc:
        status = "not_ready"
        reason_codes = [exc.reason_code]
        expected = None
    else:
        status = "ready"
        reason_codes = []
        expected = _expectation_summary(expectations)
    return {
        "schema": CAPABILITY_EXPECTATION_SCHEMA,
        "status": status,
        "reason_codes": reason_codes,
        "expected": expected,
        "boundary": {
            "external_network_call": False,
            "secret_values_exposed": False,
            "internal_endpoint_exposed": False,
        },
    }


def get_local_generation_configuration_status(
    *,
    generation_model: str,
    direct_path: str,
    rag_path: str,
    chat_mode: str,
    request_shape: str,
    capability_expectation: dict[str, Any],
) -> dict[str, Any]:
    reason_codes: list[str] = []
    expected = capability_expectation.get("expected")
    expected_generation = (
        expected.get("generation")
        if isinstance(expected, dict)
        and isinstance(expected.get("generation"), dict)
        else None
    )
    if capability_expectation.get("status") != "ready" or not expected_generation:
        for reason_code in capability_expectation.get("reason_codes") or [
            "capability_expectation_missing"
        ]:
            if reason_code not in reason_codes:
                reason_codes.append(reason_code)
    else:
        if generation_model != expected_generation.get("model"):
            reason_codes.append("capability_generation_model_mismatch")
        expected_path = expected_generation.get("api_path")
        paths_in_use = [direct_path]
        if chat_mode in {"rag", "auto"}:
            paths_in_use.append(rag_path)
        if not expected_path or any(path != expected_path for path in paths_in_use):
            reason_codes.append("capability_generation_contract_mismatch")
        actual_request_contract = _REQUEST_CONTRACT_BY_SHAPE.get(request_shape)
        if actual_request_contract != expected_generation.get("request_contract"):
            if "capability_generation_contract_mismatch" not in reason_codes:
                reason_codes.append("capability_generation_contract_mismatch")

    return {
        "schema": LOCAL_GENERATION_CONFIGURATION_SCHEMA,
        "status": "ready" if not reason_codes else "not_ready",
        "reason_codes": reason_codes,
        "generation": {
            "model": generation_model,
            "direct_path": direct_path,
            "rag_path": rag_path if chat_mode in {"rag", "auto"} else None,
            "request_contract": _REQUEST_CONTRACT_BY_SHAPE.get(request_shape),
        },
        "boundary": {
            "external_network_call": False,
            "secret_values_exposed": False,
            "internal_endpoint_exposed": False,
        },
    }


def probe_provider_runtime_capabilities(
    *,
    probe_fn: Callable[..., CapabilityProbeResult] = (
        probe_private_ai_runtime_capabilities
    ),
) -> dict[str, Any]:
    try:
        expectations = load_capability_expectations_from_env()
    except CapabilityExpectationError as exc:
        return CapabilityProbeResult.not_ready(exc.reason_code).safe_summary()

    base_url = (os.getenv("PRIVATE_AI_RUNTIME_BASE_URL") or "").strip()
    if not base_url:
        return CapabilityProbeResult.not_ready(
            "capability_endpoint_invalid"
        ).safe_summary()
    token_file = (os.getenv("PRIVATE_AI_RUNTIME_TOKEN_FILE") or "").strip()
    if not token_file:
        return CapabilityProbeResult.not_ready(
            "capability_token_missing"
        ).safe_summary()
    capabilities_path = (
        os.getenv("PRIVATE_AI_RUNTIME_CAPABILITIES_PATH") or "/v1/capabilities"
    ).strip() or "/v1/capabilities"
    timeout_raw = (
        os.getenv("PRIVATE_AI_RUNTIME_CAPABILITY_TIMEOUT_SECONDS") or "2"
    ).strip() or "2"
    try:
        timeout_seconds = float(timeout_raw)
    except ValueError:
        return CapabilityProbeResult.not_ready(
            "capability_expectation_invalid"
        ).safe_summary()
    if not 0.1 <= timeout_seconds <= 30.0:
        return CapabilityProbeResult.not_ready(
            "capability_expectation_invalid"
        ).safe_summary()

    try:
        result = probe_fn(
            base_url=base_url,
            capabilities_path=capabilities_path,
            token_file=token_file,
            expectations=expectations,
            timeout_seconds=timeout_seconds,
        )
    except Exception:
        result = CapabilityProbeResult.not_ready("capability_unreachable")
    if not isinstance(result, CapabilityProbeResult):
        result = CapabilityProbeResult.not_ready("capability_payload_malformed")
    return result.safe_summary()
