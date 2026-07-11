from __future__ import annotations

import os
import time
from typing import Callable
from urllib.parse import urlsplit

from .private_ai_runtime import PrivateAIRuntimeAdapter
from ..runtime_capabilities import (
    CapabilityExpectationError,
    CapabilityProbeResult,
    load_capability_expectations_from_env,
    probe_private_ai_runtime_capabilities,
)
from ..schemas import ProviderRequest, ProviderResult

_DEFAULT_GENERATION_MODEL = "nexus-gemma4-e4b:latest"
_DEFAULT_CAPABILITIES_PATH = "/v1/capabilities"
_DEFAULT_CAPABILITY_TIMEOUT_SECONDS = 2.0
_REQUEST_CONTRACT_BY_SHAPE = {
    "ollama_chat": "ollama.chat.v1",
    "messages": "openai.chat.v1",
    "question": "nexus.question.v1",
    "system_input": "nexus.system_input.v1",
}


class CapabilityVerifiedPrivateAIRuntimeAdapter(PrivateAIRuntimeAdapter):
    """Private Runtime adapter that proves exact upstream identity before generation."""

    def __init__(
        self,
        *,
        capability_probe: Callable[[], CapabilityProbeResult] | None = None,
    ) -> None:
        super().__init__()
        self.generation_model = (
            os.getenv(
                "PRIVATE_AI_RUNTIME_GENERATION_MODEL",
                _DEFAULT_GENERATION_MODEL,
            ).strip()
            or _DEFAULT_GENERATION_MODEL
        )
        # Generation is one capability. Retrieval is verified independently by the
        # capability manifest and is not represented as a second generation model.
        self.direct_model = self.generation_model
        self.rag_model = self.generation_model
        self.capabilities_path = (
            os.getenv(
                "PRIVATE_AI_RUNTIME_CAPABILITIES_PATH",
                _DEFAULT_CAPABILITIES_PATH,
            ).strip()
            or _DEFAULT_CAPABILITIES_PATH
        )
        self.capability_timeout_seconds = _capability_timeout_from_env()
        self._capability_probe_override = capability_probe

    def _config_error(self) -> str | None:
        parent_error = super()._config_error()
        if parent_error:
            return parent_error
        if not self.token_file:
            return "capability_token_missing"
        for name in (
            "PRIVATE_AI_RUNTIME_DIRECT_MODEL",
            "PRIVATE_AI_RUNTIME_RAG_MODEL",
        ):
            legacy = (os.getenv(name) or "").strip()
            if legacy and legacy != self.generation_model:
                return "private_ai_runtime_legacy_model_configuration_invalid"

        expected_model = (
            os.getenv("PRIVATE_AI_RUNTIME_EXPECTED_GENERATION_MODEL") or ""
        ).strip()
        if expected_model and self.generation_model != expected_model:
            return "capability_generation_model_mismatch"

        expected_path = (
            os.getenv("PRIVATE_AI_RUNTIME_EXPECTED_GENERATION_PATH") or ""
        ).strip()
        if expected_path:
            paths_in_use = [self.direct_path]
            if self.chat_mode in {"rag", "auto"}:
                paths_in_use.append(self.rag_path)
            if any(path != expected_path for path in paths_in_use):
                return "capability_generation_contract_mismatch"

        if self.chat_mode in {"rag", "auto"} and not _same_runtime_origin(
            self.base_url,
            self.rag_base_url,
        ):
            # One capability manifest proves one Runtime identity. A request must
            # never probe Runtime A and then send authoritative generation to B.
            return "capability_runtime_identity_mismatch"

        expected_request_contract = (
            os.getenv("PRIVATE_AI_RUNTIME_EXPECTED_REQUEST_CONTRACT") or ""
        ).strip()
        if expected_request_contract:
            actual_request_contract = _REQUEST_CONTRACT_BY_SHAPE.get(
                self.request_shape
            )
            if actual_request_contract != expected_request_contract:
                return "capability_generation_contract_mismatch"
        return None

    def _run_capability_probe(self) -> CapabilityProbeResult:
        if self._capability_probe_override is not None:
            try:
                result = self._capability_probe_override()
            except Exception:
                return CapabilityProbeResult.not_ready("capability_unreachable")
            if not isinstance(result, CapabilityProbeResult):
                return CapabilityProbeResult.not_ready(
                    "capability_payload_malformed"
                )
            return result
        try:
            expectations = load_capability_expectations_from_env()
        except CapabilityExpectationError as exc:
            return CapabilityProbeResult.not_ready(exc.reason_code)
        return probe_private_ai_runtime_capabilities(
            base_url=self.base_url,
            capabilities_path=self.capabilities_path,
            token_file=self.token_file,
            expectations=expectations,
            timeout_seconds=self.capability_timeout_seconds,
        )

    async def generate(self, db, request: ProviderRequest) -> ProviderResult:
        started = time.monotonic()
        config_error = self._config_error()
        if config_error:
            return self._blocked_result(
                error_code=config_error,
                started=started,
                probe_result=CapabilityProbeResult.not_ready(config_error),
            )

        expected_response_contract = (
            os.getenv("PRIVATE_AI_RUNTIME_EXPECTED_RESPONSE_CONTRACT") or ""
        ).strip()
        if (
            expected_response_contract
            and request.output_contract != expected_response_contract
        ):
            return self._blocked_result(
                error_code="capability_generation_contract_mismatch",
                started=started,
                probe_result=CapabilityProbeResult.not_ready(
                    "capability_generation_contract_mismatch"
                ),
            )

        probe_result = self._run_capability_probe()
        if not probe_result.ready:
            error_code = (
                probe_result.reason_codes[0]
                if probe_result.reason_codes
                else "capability_runtime_not_ready"
            )
            return self._blocked_result(
                error_code=error_code,
                started=started,
                probe_result=probe_result,
            )

        result = await super().generate(db, request)
        payload = (
            result.model_dump()
            if hasattr(result, "model_dump")
            else result.dict()
        )
        safe_summary = dict(payload.get("raw_payload_safe_summary") or {})
        safe_summary["runtime_capability"] = probe_result.safe_summary()
        payload["raw_payload_safe_summary"] = safe_summary
        return ProviderResult(**payload)

    def _blocked_result(
        self,
        *,
        error_code: str,
        started: float,
        probe_result: CapabilityProbeResult,
    ) -> ProviderResult:
        return ProviderResult(
            ok=False,
            provider=self.name,
            raw_provider=self.name,
            reply_source=self.name,
            model=self.generation_model,
            elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
            raw_payload_safe_summary={
                "private_ai_runtime": True,
                "error_code": error_code,
                "base_url_configured": bool(self.base_url),
                "token_file_configured": bool(self.token_file),
                "runtime_capability": probe_result.safe_summary(),
            },
            structured_output=None,
            error_code=error_code,
            retryable=False,
            fallback_allowed=False,
        )


def _capability_timeout_from_env() -> float:
    raw = (
        os.getenv("PRIVATE_AI_RUNTIME_CAPABILITY_TIMEOUT_SECONDS") or ""
    ).strip()
    if not raw:
        return _DEFAULT_CAPABILITY_TIMEOUT_SECONDS
    try:
        timeout = float(raw)
    except ValueError:
        return -1.0
    if not 0.1 <= timeout <= 30.0:
        return -1.0
    return timeout


def _same_runtime_origin(left: str, right: str) -> bool:
    try:
        left_parts = urlsplit(left)
        right_parts = urlsplit(right)
    except ValueError:
        return False
    return (
        left_parts.scheme.lower(),
        left_parts.hostname or "",
        left_parts.port,
    ) == (
        right_parts.scheme.lower(),
        right_parts.hostname or "",
        right_parts.port,
    )
