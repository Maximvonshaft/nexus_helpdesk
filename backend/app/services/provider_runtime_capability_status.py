from __future__ import annotations

import os
from typing import Any, Callable

from .provider_runtime.runtime_capabilities import (
    CAPABILITY_SCHEMA,
    CapabilityExpectationError,
    CapabilityProbeResult,
    RuntimeCapabilityExpectations,
    load_capability_expectations_from_env,
    probe_private_ai_runtime_capabilities,
)

CAPABILITY_EXPECTATION_SCHEMA = "nexus.ai_runtime.capability_expectation.v1"


def _expectation_summary(expectations: RuntimeCapabilityExpectations) -> dict[str, Any]:
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


def probe_provider_runtime_capabilities(
    *,
    probe_fn: Callable[..., CapabilityProbeResult] = probe_private_ai_runtime_capabilities,
) -> dict[str, Any]:
    try:
        expectations = load_capability_expectations_from_env()
    except CapabilityExpectationError as exc:
        return CapabilityProbeResult.not_ready(exc.reason_code).safe_summary()

    base_url = (os.getenv("PRIVATE_AI_RUNTIME_BASE_URL") or "").strip()
    if not base_url:
        return CapabilityProbeResult.not_ready("capability_endpoint_invalid").safe_summary()
    token_file = (os.getenv("PRIVATE_AI_RUNTIME_TOKEN_FILE") or "").strip()
    if not token_file:
        return CapabilityProbeResult.not_ready("capability_token_missing").safe_summary()
    capabilities_path = (
        os.getenv("PRIVATE_AI_RUNTIME_CAPABILITIES_PATH") or "/v1/capabilities"
    ).strip() or "/v1/capabilities"
    timeout_raw = (
        os.getenv("PRIVATE_AI_RUNTIME_CAPABILITY_TIMEOUT_SECONDS") or "2"
    ).strip() or "2"
    try:
        timeout_seconds = float(timeout_raw)
    except ValueError:
        return CapabilityProbeResult.not_ready("capability_expectation_invalid").safe_summary()
    if not 0.1 <= timeout_seconds <= 30.0:
        return CapabilityProbeResult.not_ready("capability_expectation_invalid").safe_summary()

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
