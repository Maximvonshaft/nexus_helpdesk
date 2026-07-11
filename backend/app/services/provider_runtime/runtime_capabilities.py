from __future__ import annotations

import json
import os
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

CAPABILITY_SCHEMA = "nexus.ai_runtime.capabilities.v1"
CAPABILITY_PROBE_SCHEMA = "nexus.ai_runtime.capability_probe.v1"
MAX_CAPABILITY_BYTES = 32 * 1024
_MAX_IDENTIFIER_CHARS = 128
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,127}$")
_SECRET_KEY_FRAGMENTS = (
    "token",
    "authorization",
    "password",
    "credential",
    "secret",
    "api_key",
    "base_url",
    "endpoint_url",
)
_RUNTIME_READINESS_REASON_CODES = frozenset(
    {
        "runtime_model_loading",
        "runtime_retrieval_unavailable",
        "runtime_voice_unavailable",
        "runtime_configuration_invalid",
        "runtime_dependency_unavailable",
        "runtime_maintenance",
        "runtime_degraded",
    }
)
_EXPECTATION_ENV = {
    "runtime_id": "PRIVATE_AI_RUNTIME_EXPECTED_RUNTIME_ID",
    "runtime_version": "PRIVATE_AI_RUNTIME_EXPECTED_RUNTIME_VERSION",
    "generation_model": "PRIVATE_AI_RUNTIME_EXPECTED_GENERATION_MODEL",
    "generation_api_path": "PRIVATE_AI_RUNTIME_EXPECTED_GENERATION_PATH",
    "request_contract": "PRIVATE_AI_RUNTIME_EXPECTED_REQUEST_CONTRACT",
    "response_contract": "PRIVATE_AI_RUNTIME_EXPECTED_RESPONSE_CONTRACT",
    "retrieval_backend": "PRIVATE_AI_RUNTIME_EXPECTED_RETRIEVAL_BACKEND",
    "embedding_model": "PRIVATE_AI_RUNTIME_EXPECTED_EMBEDDING_MODEL",
    "embedding_dimension": "PRIVATE_AI_RUNTIME_EXPECTED_EMBEDDING_DIMENSION",
    "reranker_model": "PRIVATE_AI_RUNTIME_EXPECTED_RERANKER_MODEL",
    "collection_alias": "PRIVATE_AI_RUNTIME_EXPECTED_COLLECTION_ALIAS",
}


class CapabilityManifestError(ValueError):
    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


class CapabilityExpectationError(ValueError):
    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class RuntimeIdentity:
    id: str
    version: str


@dataclass(frozen=True)
class RuntimeReadiness:
    state: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class GenerationCapability:
    model: str
    structured_output: bool
    api_path: str
    request_contract: str
    response_contract: str


@dataclass(frozen=True)
class RetrievalCapability:
    enabled: bool
    backend: str | None
    embedding_model: str | None
    embedding_dimension: int | None
    reranker_enabled: bool
    reranker_model: str | None
    collection_alias: str | None


@dataclass(frozen=True)
class VoiceComponentCapability:
    enabled: bool
    model: str | None


@dataclass(frozen=True)
class VoiceCapability:
    stt: VoiceComponentCapability
    tts: VoiceComponentCapability
    live_voice: bool


@dataclass(frozen=True)
class RuntimeCapabilityManifest:
    schema: str
    runtime: RuntimeIdentity
    readiness: RuntimeReadiness
    generation: GenerationCapability
    retrieval: RetrievalCapability
    voice: VoiceCapability

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "runtime": {"id": self.runtime.id, "version": self.runtime.version},
            "readiness": {
                "state": self.readiness.state,
                "reason_codes": list(self.readiness.reason_codes),
            },
            "generation": {
                "model": self.generation.model,
                "structured_output": self.generation.structured_output,
                "api_path": self.generation.api_path,
                "request_contract": self.generation.request_contract,
                "response_contract": self.generation.response_contract,
            },
            "retrieval": {
                "enabled": self.retrieval.enabled,
                "backend": self.retrieval.backend,
                "embedding_model": self.retrieval.embedding_model,
                "embedding_dimension": self.retrieval.embedding_dimension,
                "reranker_enabled": self.retrieval.reranker_enabled,
                "reranker_model": self.retrieval.reranker_model,
                "collection_alias": self.retrieval.collection_alias,
            },
            "voice": {
                "stt": {"enabled": self.voice.stt.enabled, "model": self.voice.stt.model},
                "tts": {"enabled": self.voice.tts.enabled, "model": self.voice.tts.model},
                "live_voice": self.voice.live_voice,
            },
        }


@dataclass(frozen=True)
class RuntimeCapabilityExpectations:
    schema: str
    runtime_id: str
    runtime_version: str
    generation_model: str
    generation_api_path: str
    request_contract: str
    response_contract: str
    retrieval_backend: str
    embedding_model: str
    embedding_dimension: int
    reranker_model: str
    collection_alias: str


@dataclass(frozen=True)
class CapabilityProbeResult:
    ready: bool
    reason_codes: tuple[str, ...]
    manifest: RuntimeCapabilityManifest | None = None

    @classmethod
    def not_ready(cls, reason_code: str) -> "CapabilityProbeResult":
        return cls(ready=False, reason_codes=(reason_code,), manifest=None)

    def safe_summary(self) -> dict[str, Any]:
        manifest = self.manifest
        if manifest is None:
            runtime = {"id": None, "version": None}
            generation = {
                "available": False,
                "model": None,
                "structured_output": False,
                "api_path": None,
                "request_contract": None,
                "response_contract": None,
            }
            retrieval = {
                "available": False,
                "backend": None,
                "embedding_model": None,
                "embedding_dimension": None,
                "reranker_available": False,
                "reranker_model": None,
                "collection_alias": None,
            }
            voice = {
                "stt_available": False,
                "stt_model": None,
                "tts_available": False,
                "tts_model": None,
                "live_voice": False,
            }
        else:
            runtime = {"id": manifest.runtime.id, "version": manifest.runtime.version}
            generation = {
                "available": True,
                "model": manifest.generation.model,
                "structured_output": manifest.generation.structured_output,
                "api_path": manifest.generation.api_path,
                "request_contract": manifest.generation.request_contract,
                "response_contract": manifest.generation.response_contract,
            }
            retrieval = {
                "available": manifest.retrieval.enabled,
                "backend": manifest.retrieval.backend,
                "embedding_model": manifest.retrieval.embedding_model,
                "embedding_dimension": manifest.retrieval.embedding_dimension,
                "reranker_available": manifest.retrieval.reranker_enabled,
                "reranker_model": manifest.retrieval.reranker_model,
                "collection_alias": manifest.retrieval.collection_alias,
            }
            voice = {
                "stt_available": manifest.voice.stt.enabled,
                "stt_model": manifest.voice.stt.model,
                "tts_available": manifest.voice.tts.enabled,
                "tts_model": manifest.voice.tts.model,
                "live_voice": manifest.voice.live_voice,
            }
        return {
            "schema": CAPABILITY_PROBE_SCHEMA,
            "status": "ready" if self.ready else "not_ready",
            "reason_codes": list(self.reason_codes),
            "runtime": runtime,
            "generation": generation,
            "retrieval": retrieval,
            "voice": voice,
            "boundary": {
                "secret_values_exposed": False,
                "internal_endpoint_exposed": False,
                "raw_manifest_exposed": False,
            },
        }


class _DuplicateKeyError(ValueError):
    pass


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _pairs_to_unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError
        result[key] = value
    return result


def _expect_exact_keys(value: Any, expected: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != expected:
        raise CapabilityManifestError("capability_payload_malformed")
    return value


def _reject_secret_like_keys(value: Any) -> None:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            if not isinstance(raw_key, str):
                raise CapabilityManifestError("capability_payload_malformed")
            key = raw_key.strip().lower()
            if any(fragment in key for fragment in _SECRET_KEY_FRAGMENTS):
                raise CapabilityManifestError("capability_payload_malformed")
            _reject_secret_like_keys(child)
    elif isinstance(value, list):
        for child in value:
            _reject_secret_like_keys(child)


def _identifier(value: Any) -> str:
    if not isinstance(value, str):
        raise CapabilityManifestError("capability_payload_malformed")
    candidate = value.strip()
    if candidate != value or not _IDENTIFIER_RE.fullmatch(candidate):
        raise CapabilityManifestError("capability_payload_malformed")
    return candidate


def _optional_identifier(value: Any, *, enabled: bool) -> str | None:
    if enabled:
        return _identifier(value)
    if value is not None:
        raise CapabilityManifestError("capability_payload_malformed")
    return None


def _strict_bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise CapabilityManifestError("capability_payload_malformed")
    return value


def _api_path(value: Any, *, manifest_error: bool = True) -> str:
    error = CapabilityManifestError if manifest_error else CapabilityExpectationError
    if not isinstance(value, str) or not value or len(value) > _MAX_IDENTIFIER_CHARS:
        raise error("capability_payload_malformed" if manifest_error else "capability_expectation_invalid")
    parsed = urlsplit(value)
    if (
        not value.startswith("/")
        or value.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.path != value
    ):
        raise error("capability_payload_malformed" if manifest_error else "capability_expectation_invalid")
    return value


def _dimension(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65536:
        raise CapabilityManifestError("capability_payload_malformed")
    return value


def parse_capability_manifest(raw: bytes | str) -> RuntimeCapabilityManifest:
    if isinstance(raw, bytes):
        if len(raw) > MAX_CAPABILITY_BYTES:
            raise CapabilityManifestError("capability_payload_too_large")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CapabilityManifestError("capability_payload_malformed") from exc
    elif isinstance(raw, str):
        if len(raw.encode("utf-8")) > MAX_CAPABILITY_BYTES:
            raise CapabilityManifestError("capability_payload_too_large")
        text = raw
    else:
        raise CapabilityManifestError("capability_payload_malformed")

    try:
        payload = json.loads(text, object_pairs_hook=_pairs_to_unique_object)
    except (json.JSONDecodeError, _DuplicateKeyError, TypeError, ValueError) as exc:
        raise CapabilityManifestError("capability_payload_malformed") from exc

    _reject_secret_like_keys(payload)
    root = _expect_exact_keys(
        payload,
        frozenset({"schema", "runtime", "readiness", "generation", "retrieval", "voice"}),
    )
    runtime = _expect_exact_keys(root["runtime"], frozenset({"id", "version"}))
    readiness = _expect_exact_keys(root["readiness"], frozenset({"state", "reason_codes"}))
    generation = _expect_exact_keys(
        root["generation"],
        frozenset(
            {
                "model",
                "structured_output",
                "api_path",
                "request_contract",
                "response_contract",
            }
        ),
    )
    retrieval = _expect_exact_keys(
        root["retrieval"],
        frozenset(
            {
                "enabled",
                "backend",
                "embedding_model",
                "embedding_dimension",
                "reranker_enabled",
                "reranker_model",
                "collection_alias",
            }
        ),
    )
    voice = _expect_exact_keys(root["voice"], frozenset({"stt", "tts", "live_voice"}))
    stt = _expect_exact_keys(voice["stt"], frozenset({"enabled", "model"}))
    tts = _expect_exact_keys(voice["tts"], frozenset({"enabled", "model"}))

    state = readiness["state"]
    if state not in {"ready", "not_ready"}:
        raise CapabilityManifestError("capability_payload_malformed")
    reason_codes_value = readiness["reason_codes"]
    if not isinstance(reason_codes_value, list) or len(reason_codes_value) > 16:
        raise CapabilityManifestError("capability_payload_malformed")
    if any(not isinstance(code, str) or code not in _RUNTIME_READINESS_REASON_CODES for code in reason_codes_value):
        raise CapabilityManifestError("capability_payload_malformed")
    if len(set(reason_codes_value)) != len(reason_codes_value):
        raise CapabilityManifestError("capability_payload_malformed")
    if (state == "ready" and reason_codes_value) or (state == "not_ready" and not reason_codes_value):
        raise CapabilityManifestError("capability_payload_malformed")

    retrieval_enabled = _strict_bool(retrieval["enabled"])
    reranker_enabled = _strict_bool(retrieval["reranker_enabled"])
    if not retrieval_enabled and reranker_enabled:
        raise CapabilityManifestError("capability_payload_malformed")
    if retrieval_enabled:
        backend = _identifier(retrieval["backend"])
        embedding_model = _identifier(retrieval["embedding_model"])
        embedding_dimension = _dimension(retrieval["embedding_dimension"])
        collection_alias = _identifier(retrieval["collection_alias"])
        reranker_model = _optional_identifier(retrieval["reranker_model"], enabled=reranker_enabled)
    else:
        if any(
            retrieval[name] is not None
            for name in ("backend", "embedding_model", "embedding_dimension", "reranker_model", "collection_alias")
        ):
            raise CapabilityManifestError("capability_payload_malformed")
        backend = None
        embedding_model = None
        embedding_dimension = None
        reranker_model = None
        collection_alias = None

    stt_enabled = _strict_bool(stt["enabled"])
    tts_enabled = _strict_bool(tts["enabled"])

    return RuntimeCapabilityManifest(
        schema=_identifier(root["schema"]),
        runtime=RuntimeIdentity(id=_identifier(runtime["id"]), version=_identifier(runtime["version"])),
        readiness=RuntimeReadiness(state=state, reason_codes=tuple(reason_codes_value)),
        generation=GenerationCapability(
            model=_identifier(generation["model"]),
            structured_output=_strict_bool(generation["structured_output"]),
            api_path=_api_path(generation["api_path"]),
            request_contract=_identifier(generation["request_contract"]),
            response_contract=_identifier(generation["response_contract"]),
        ),
        retrieval=RetrievalCapability(
            enabled=retrieval_enabled,
            backend=backend,
            embedding_model=embedding_model,
            embedding_dimension=embedding_dimension,
            reranker_enabled=reranker_enabled,
            reranker_model=reranker_model,
            collection_alias=collection_alias,
        ),
        voice=VoiceCapability(
            stt=VoiceComponentCapability(
                enabled=stt_enabled,
                model=_optional_identifier(stt["model"], enabled=stt_enabled),
            ),
            tts=VoiceComponentCapability(
                enabled=tts_enabled,
                model=_optional_identifier(tts["model"], enabled=tts_enabled),
            ),
            live_voice=_strict_bool(voice["live_voice"]),
        ),
    )


def _expectation_identifier(value: str) -> str:
    if not isinstance(value, str):
        raise CapabilityExpectationError("capability_expectation_invalid")
    candidate = value.strip()
    if candidate != value or not _IDENTIFIER_RE.fullmatch(candidate):
        raise CapabilityExpectationError("capability_expectation_invalid")
    return candidate


def load_capability_expectations_from_env() -> RuntimeCapabilityExpectations:
    values: dict[str, Any] = {}
    for field_name, env_name in _EXPECTATION_ENV.items():
        raw = os.getenv(env_name)
        if raw is None or not raw.strip():
            raise CapabilityExpectationError("capability_expectation_missing")
        values[field_name] = raw.strip()

    try:
        dimension = int(values["embedding_dimension"])
    except (TypeError, ValueError) as exc:
        raise CapabilityExpectationError("capability_expectation_invalid") from exc
    if str(dimension) != values["embedding_dimension"] or not 1 <= dimension <= 65536:
        raise CapabilityExpectationError("capability_expectation_invalid")

    for field_name in (
        "runtime_id",
        "runtime_version",
        "generation_model",
        "request_contract",
        "response_contract",
        "retrieval_backend",
        "embedding_model",
        "reranker_model",
        "collection_alias",
    ):
        values[field_name] = _expectation_identifier(values[field_name])
    values["generation_api_path"] = _api_path(values["generation_api_path"], manifest_error=False)
    values["embedding_dimension"] = dimension
    values["schema"] = CAPABILITY_SCHEMA
    return RuntimeCapabilityExpectations(**values)


def evaluate_capability_manifest(
    manifest: RuntimeCapabilityManifest,
    expectations: RuntimeCapabilityExpectations,
) -> CapabilityProbeResult:
    reasons: list[str] = []

    def add(condition: bool, reason_code: str) -> None:
        if condition and reason_code not in reasons:
            reasons.append(reason_code)

    add(manifest.schema != expectations.schema, "capability_schema_unsupported")
    add(manifest.runtime.id != expectations.runtime_id, "capability_runtime_identity_mismatch")
    add(manifest.runtime.version != expectations.runtime_version, "capability_runtime_version_mismatch")
    add(manifest.readiness.state != "ready", "capability_runtime_not_ready")
    add(manifest.generation.model != expectations.generation_model, "capability_generation_model_mismatch")
    add(
        not manifest.generation.structured_output
        or manifest.generation.api_path != expectations.generation_api_path
        or manifest.generation.request_contract != expectations.request_contract
        or manifest.generation.response_contract != expectations.response_contract,
        "capability_generation_contract_mismatch",
    )
    add(
        not manifest.retrieval.enabled
        or manifest.retrieval.backend != expectations.retrieval_backend,
        "capability_retrieval_backend_mismatch",
    )
    add(
        manifest.retrieval.embedding_model != expectations.embedding_model,
        "capability_embedding_model_mismatch",
    )
    add(
        manifest.retrieval.embedding_dimension != expectations.embedding_dimension,
        "capability_embedding_dimension_mismatch",
    )
    if not manifest.retrieval.reranker_enabled:
        add(True, "capability_reranker_missing")
    else:
        add(
            manifest.retrieval.reranker_model != expectations.reranker_model,
            "capability_reranker_model_mismatch",
        )
    add(
        manifest.retrieval.collection_alias != expectations.collection_alias,
        "capability_collection_alias_mismatch",
    )
    return CapabilityProbeResult(ready=not reasons, reason_codes=tuple(reasons), manifest=manifest)


def build_capability_url(base_url: str, capabilities_path: str) -> str:
    try:
        parsed_base = urlsplit(base_url)
        path = _api_path(capabilities_path, manifest_error=False)
    except CapabilityExpectationError as exc:
        raise CapabilityExpectationError("capability_endpoint_invalid") from exc
    except Exception as exc:
        raise CapabilityExpectationError("capability_endpoint_invalid") from exc
    if (
        parsed_base.scheme not in {"http", "https"}
        or not parsed_base.hostname
        or parsed_base.username is not None
        or parsed_base.password is not None
        or parsed_base.query
        or parsed_base.fragment
    ):
        raise CapabilityExpectationError("capability_endpoint_invalid")
    authority = parsed_base.hostname
    if parsed_base.port is not None:
        authority = f"{authority}:{parsed_base.port}"
    return urlunsplit((parsed_base.scheme, authority, path, "", ""))


def _token_from_file(token_file: str) -> str | None:
    if not token_file:
        return None
    try:
        token = Path(token_file).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    if not token or len(token) > 4096 or any(char in token for char in "\r\n"):
        return None
    return token


def probe_private_ai_runtime_capabilities(
    *,
    base_url: str,
    capabilities_path: str,
    token_file: str,
    expectations: RuntimeCapabilityExpectations,
    timeout_seconds: int | float,
    opener: Any | None = None,
) -> CapabilityProbeResult:
    token = _token_from_file(token_file)
    if token is None:
        return CapabilityProbeResult.not_ready("capability_token_missing")
    try:
        endpoint = build_capability_url(base_url, capabilities_path)
    except CapabilityExpectationError:
        return CapabilityProbeResult.not_ready("capability_endpoint_invalid")
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError):
        return CapabilityProbeResult.not_ready("capability_expectation_invalid")
    if not 0.1 <= timeout <= 30.0:
        return CapabilityProbeResult.not_ready("capability_expectation_invalid")

    request = urllib.request.Request(
        endpoint,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    client = opener or urllib.request.build_opener(_NoRedirectHandler())
    try:
        with client.open(request, timeout=timeout) as response:
            if int(getattr(response, "status", 200)) != 200:
                return CapabilityProbeResult.not_ready("capability_http_error")
            content_type = str(response.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                return CapabilityProbeResult.not_ready("capability_content_type_invalid")
            raw = response.read(MAX_CAPABILITY_BYTES + 1)
    except (TimeoutError, socket.timeout):
        return CapabilityProbeResult.not_ready("capability_timeout")
    except urllib.error.HTTPError:
        return CapabilityProbeResult.not_ready("capability_http_error")
    except (urllib.error.URLError, OSError, ValueError):
        return CapabilityProbeResult.not_ready("capability_unreachable")

    if len(raw) > MAX_CAPABILITY_BYTES:
        return CapabilityProbeResult.not_ready("capability_payload_too_large")
    try:
        manifest = parse_capability_manifest(raw)
    except CapabilityManifestError as exc:
        return CapabilityProbeResult.not_ready(exc.reason_code)
    return evaluate_capability_manifest(manifest, expectations)
