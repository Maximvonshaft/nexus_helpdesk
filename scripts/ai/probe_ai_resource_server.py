#!/usr/bin/env python3
"""Read-only, bounded AI resource server capability probe for Nexus OSR.

The probe only touches operator-declared URLs. Passive mode performs bounded
GET/OPTIONS requests. Active mode performs small, synthetic inference requests
and never invokes RAG ingestion, vector writes, tools, actions, or outbound
operations. Raw provider bodies, prompts, transcripts, generated audio, and
credentials are never written to the report.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

REPORT_SCHEMA = "nexus.ai_resource_probe.v1"
CONFIG_SCHEMA = "nexus.ai_resource_probe.config.v1"
MAX_TARGETS = 32
MAX_MODELS = 200
MAX_SAFE_KEYS = 80
DEFAULT_TIMEOUT_SECONDS = 8
DEFAULT_MAX_RESPONSE_BYTES = 256 * 1024
DEFAULT_MAX_ACTIVE_CALLS = 12
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
SAFE_HEADER_RE = re.compile(r"^[A-Za-z0-9-]{1,80}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

FIXED_LLM_PROMPT = 'Return exactly one JSON object with the key "probe" and value "ok". Do not call tools.'
FIXED_RAG_QUESTION = "What service information is available in the indexed knowledge? Return a short JSON response."
FIXED_EMBEDDING_TEXT = "nexus capability probe"
FIXED_TTS_TEXT = "Nexus AI capability probe."

WRITE_PATH_MARKERS = (
    "/upsert",
    "/insert",
    "/delete",
    "/collections/create",
    "/collections/delete",
    "/index",
    "/ingest",
    "/upload",
    "/tool",
    "/action",
    "/send",
)

PASSIVE_PATHS: dict[str, tuple[tuple[str, str], ...]] = {
    "common": (
        ("GET", "/"),
        ("GET", "/health"),
        ("GET", "/healthz"),
        ("GET", "/ready"),
        ("GET", "/readyz"),
        ("GET", "/version"),
    ),
    "openai": (
        ("GET", "/v1/models"),
        ("OPTIONS", "/v1/responses"),
        ("OPTIONS", "/v1/chat/completions"),
        ("OPTIONS", "/v1/embeddings"),
        ("OPTIONS", "/v1/audio/transcriptions"),
        ("OPTIONS", "/v1/audio/speech"),
    ),
    "ollama": (
        ("GET", "/api/version"),
        ("GET", "/api/tags"),
        ("GET", "/api/ps"),
        ("OPTIONS", "/api/chat"),
        ("OPTIONS", "/api/embed"),
        ("OPTIONS", "/api/embeddings"),
    ),
    "nexus_runtime": (
        ("OPTIONS", "/api/chat"),
        ("OPTIONS", "/chat/direct"),
        ("OPTIONS", "/chat/rag"),
        ("GET", "/rag/health"),
        ("GET", "/rag/status"),
        ("GET", "/rag/stats"),
        ("GET", "/rag/capabilities"),
    ),
    "vector": (
        ("GET", "/collections"),
        ("GET", "/v1/meta"),
        ("GET", "/v1/.well-known/ready"),
        ("GET", "/v1/schema"),
        ("GET", "/api/v2/heartbeat"),
        ("GET", "/api/v1/heartbeat"),
        ("GET", "/api/v2/version"),
    ),
}

ACTIVE_TEST_NAMES = {
    "openai_chat",
    "openai_responses",
    "openai_embeddings",
    "openai_stt",
    "openai_tts",
    "ollama_chat",
    "ollama_embeddings",
    "nexus_llm_bridge",
    "nexus_rag_question",
    "nexus_stt_bridge",
    "nexus_tts_bridge",
}


class ProbeConfigError(ValueError):
    pass


class ResponseTooLarge(RuntimeError):
    pass


class CrossOriginRedirect(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthConfig:
    kind: str = "none"
    env: str | None = None
    file: str | None = None
    header_name: str = "Authorization"
    prefix: str = "Bearer "

    def resolve(self) -> tuple[str | None, str | None]:
        if self.kind == "none":
            return None, None
        value = ""
        source = None
        if self.env:
            value = (os.getenv(self.env) or "").strip()
            if value:
                source = f"env:{self.env}"
        if not value and self.file:
            try:
                value = Path(self.file).read_text(encoding="utf-8").strip()
            except OSError:
                value = ""
            if value:
                source = "file"
        if not value:
            return None, None
        if self.kind == "bearer":
            if value.lower().startswith("bearer "):
                value = value.split(None, 1)[1].strip()
            return self.header_name, f"{self.prefix}{value}"
        if self.kind == "header":
            return self.header_name, f"{self.prefix}{value}"
        raise ProbeConfigError("unsupported_auth_kind")


@dataclass(frozen=True)
class TargetConfig:
    name: str
    base_url: str
    profiles: tuple[str, ...] = ("auto",)
    auth: AuthConfig = AuthConfig()
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    verify_tls: bool = True
    mode: str = "passive"
    active_tests: tuple[str, ...] = ()
    max_active_calls: int = DEFAULT_MAX_ACTIVE_CALLS
    models: Mapping[str, str] = field(default_factory=dict)
    endpoints: Mapping[str, str] = field(default_factory=dict)
    stt_sample_file: str | None = None
    websocket_url: str | None = None


@dataclass(frozen=True)
class ProbeConfig:
    targets: tuple[TargetConfig, ...]
    output: str | None = None


@dataclass(frozen=True)
class HttpResponse:
    url: str
    method: str
    status: int | None
    headers: Mapping[str, str]
    body: bytes
    latency_ms: int
    error_code: str | None = None


class Transport(Protocol):
    def request(
        self,
        *,
        target: TargetConfig,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> HttpResponse:
        ...


class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if _origin(old) != _origin(new):
            raise CrossOriginRedirect("cross_origin_redirect_rejected")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class UrllibTransport:
    def request(
        self,
        *,
        target: TargetConfig,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> HttpResponse:
        started = time.monotonic()
        request_headers = {
            "Accept": "application/json, audio/*;q=0.8, text/plain;q=0.5, */*;q=0.1",
            "User-Agent": "Nexus-AI-Resource-Probe/1",
            **dict(headers or {}),
        }
        auth_name, auth_value = target.auth.resolve()
        if auth_name and auth_value:
            request_headers[auth_name] = auth_value
        context = ssl.create_default_context() if target.verify_tls else ssl._create_unverified_context()  # noqa: SLF001
        opener = urllib.request.build_opener(
            SameOriginRedirectHandler(),
            urllib.request.HTTPSHandler(context=context),
        )
        request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        try:
            with opener.open(request, timeout=target.timeout_seconds) as response:
                response_body = _read_bounded(response, target.max_response_bytes)
                return HttpResponse(
                    url=url,
                    method=method,
                    status=int(response.status),
                    headers={str(k): str(v) for k, v in response.headers.items()},
                    body=response_body,
                    latency_ms=_elapsed_ms(started),
                )
        except urllib.error.HTTPError as exc:
            try:
                response_body = _read_bounded(exc, target.max_response_bytes)
            except ResponseTooLarge:
                response_body = b""
            return HttpResponse(
                url=url,
                method=method,
                status=int(exc.code),
                headers={str(k): str(v) for k, v in (exc.headers.items() if exc.headers else [])},
                body=response_body,
                latency_ms=_elapsed_ms(started),
                error_code=f"http_{exc.code}",
            )
        except CrossOriginRedirect:
            return HttpResponse(url, method, None, {}, b"", _elapsed_ms(started), "cross_origin_redirect")
        except ResponseTooLarge:
            return HttpResponse(url, method, None, {}, b"", _elapsed_ms(started), "response_too_large")
        except socket.timeout:
            return HttpResponse(url, method, None, {}, b"", _elapsed_ms(started), "timeout")
        except ssl.SSLError:
            return HttpResponse(url, method, None, {}, b"", _elapsed_ms(started), "tls_error")
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            code = "timeout" if isinstance(reason, socket.timeout) else "unreachable"
            return HttpResponse(url, method, None, {}, b"", _elapsed_ms(started), code)
        except OSError as exc:
            return HttpResponse(url, method, None, {}, b"", _elapsed_ms(started), f"network_{exc.__class__.__name__}")


def _read_bounded(response: Any, maximum: int) -> bytes:
    data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ResponseTooLarge("response_too_large")
    return data


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _origin(parsed: urllib.parse.SplitResult) -> tuple[str, str, int | None]:
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port


def _safe_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit((parsed.scheme.lower(), f"{host}{port}", path, "", ""))


def _join_target_url(base_url: str, path: str) -> str:
    base = urllib.parse.urlsplit(base_url)
    if path.startswith("http://") or path.startswith("https://"):
        candidate = urllib.parse.urlsplit(path)
        if _origin(candidate) != _origin(base):
            raise ProbeConfigError("cross_origin_endpoint_requires_separate_target")
        return _safe_url(path)
    if not path.startswith("/"):
        base_path = base.path.rstrip("/")
        joined_path = f"{base_path}/{path}" if base_path else f"/{path}"
    else:
        joined_path = path
    return urllib.parse.urlunsplit((base.scheme, base.netloc, joined_path, "", ""))


def _validate_base_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ProbeConfigError("target_base_url_invalid")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProbeConfigError("target_base_url_must_not_contain_credentials_query_or_fragment")
    return _safe_url(value)


def _parse_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise ProbeConfigError("invalid_boolean")


def _parse_auth(raw: Any) -> AuthConfig:
    if raw in (None, {}):
        return AuthConfig()
    if not isinstance(raw, dict):
        raise ProbeConfigError("auth_must_be_object")
    kind = str(raw.get("type") or "none").strip().lower()
    if kind not in {"none", "bearer", "header"}:
        raise ProbeConfigError("unsupported_auth_kind")
    env = str(raw.get("env") or "").strip() or None
    file_path = str(raw.get("file") or "").strip() or None
    header_name = str(raw.get("header_name") or "Authorization").strip()
    prefix = str(raw.get("prefix") if raw.get("prefix") is not None else ("Bearer " if kind == "bearer" else ""))
    if not SAFE_HEADER_RE.fullmatch(header_name):
        raise ProbeConfigError("auth_header_name_invalid")
    if len(prefix) > 32 or any(ord(ch) < 32 for ch in prefix):
        raise ProbeConfigError("auth_prefix_invalid")
    return AuthConfig(kind=kind, env=env, file=file_path, header_name=header_name, prefix=prefix)


def load_config(path: Path) -> ProbeConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProbeConfigError("config_invalid_json") from exc
    return parse_config(payload)


def parse_config(payload: Any) -> ProbeConfig:
    if not isinstance(payload, dict) or payload.get("schema") != CONFIG_SCHEMA:
        raise ProbeConfigError("config_schema_invalid")
    raw_targets = payload.get("targets")
    if not isinstance(raw_targets, list) or not 1 <= len(raw_targets) <= MAX_TARGETS:
        raise ProbeConfigError("config_targets_invalid")
    targets: list[TargetConfig] = []
    seen: set[str] = set()
    global_mode = str(payload.get("mode") or "passive").strip().lower()
    global_timeout = int(payload.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
    global_max = int(payload.get("max_response_bytes") or DEFAULT_MAX_RESPONSE_BYTES)
    global_verify_tls = _parse_bool(payload.get("verify_tls"), default=True)
    for raw in raw_targets:
        if not isinstance(raw, dict):
            raise ProbeConfigError("target_must_be_object")
        name = str(raw.get("name") or "").strip()
        if not SAFE_NAME_RE.fullmatch(name) or name in seen:
            raise ProbeConfigError("target_name_invalid_or_duplicate")
        seen.add(name)
        base_url = _validate_base_url(str(raw.get("base_url") or ""))
        profiles_raw = raw.get("profiles", ["auto"])
        if not isinstance(profiles_raw, list) or not profiles_raw:
            raise ProbeConfigError("target_profiles_invalid")
        profiles = tuple(str(item).strip().lower() for item in profiles_raw if str(item).strip())
        allowed_profiles = {"auto", "common", "openai", "ollama", "nexus_runtime", "vector", "voice"}
        if not profiles or any(item not in allowed_profiles for item in profiles):
            raise ProbeConfigError("target_profile_unsupported")
        mode = str(raw.get("mode") or global_mode).strip().lower()
        if mode not in {"passive", "active"}:
            raise ProbeConfigError("target_mode_invalid")
        active_raw = raw.get("active_tests", [])
        if active_raw == "auto":
            active_tests = ("auto",)
        elif isinstance(active_raw, list):
            active_tests = tuple(str(item).strip().lower() for item in active_raw if str(item).strip())
        else:
            raise ProbeConfigError("active_tests_invalid")
        if any(item != "auto" and item not in ACTIVE_TEST_NAMES for item in active_tests):
            raise ProbeConfigError("active_test_unsupported")
        models = raw.get("models") or {}
        endpoints = raw.get("endpoints") or {}
        if not isinstance(models, dict) or not isinstance(endpoints, dict):
            raise ProbeConfigError("target_models_or_endpoints_invalid")
        safe_models = {
            str(k): str(v).strip()
            for k, v in models.items()
            if SAFE_NAME_RE.fullmatch(str(k)) and str(v).strip() and len(str(v).strip()) <= 200
        }
        safe_endpoints: dict[str, str] = {}
        for key, value in endpoints.items():
            key_str = str(key).strip()
            value_str = str(value).strip()
            if not SAFE_NAME_RE.fullmatch(key_str) or not value_str:
                raise ProbeConfigError("target_endpoint_invalid")
            candidate = _join_target_url(base_url, value_str)
            path = urllib.parse.urlsplit(candidate).path.lower()
            if any(marker in path for marker in WRITE_PATH_MARKERS):
                if key_str not in {"rag_upsert_declared"}:
                    raise ProbeConfigError("write_endpoint_forbidden")
            safe_endpoints[key_str] = candidate
        timeout_seconds = int(raw.get("timeout_seconds") or global_timeout)
        max_response_bytes = int(raw.get("max_response_bytes") or global_max)
        max_active_calls = int(raw.get("max_active_calls") or DEFAULT_MAX_ACTIVE_CALLS)
        if not 1 <= timeout_seconds <= 60:
            raise ProbeConfigError("timeout_out_of_range")
        if not 1024 <= max_response_bytes <= 2 * 1024 * 1024:
            raise ProbeConfigError("max_response_bytes_out_of_range")
        if not 1 <= max_active_calls <= 32:
            raise ProbeConfigError("max_active_calls_out_of_range")
        websocket_url = str(raw.get("websocket_url") or "").strip() or None
        if websocket_url:
            parsed_ws = urllib.parse.urlsplit(websocket_url)
            if parsed_ws.scheme not in {"ws", "wss"} or not parsed_ws.hostname or parsed_ws.username or parsed_ws.password:
                raise ProbeConfigError("websocket_url_invalid")
        targets.append(
            TargetConfig(
                name=name,
                base_url=base_url,
                profiles=profiles,
                auth=_parse_auth(raw.get("auth")),
                timeout_seconds=timeout_seconds,
                max_response_bytes=max_response_bytes,
                verify_tls=_parse_bool(raw.get("verify_tls"), default=global_verify_tls),
                mode=mode,
                active_tests=active_tests,
                max_active_calls=max_active_calls,
                models=safe_models,
                endpoints=safe_endpoints,
                stt_sample_file=str(raw.get("stt_sample_file") or "").strip() or None,
                websocket_url=websocket_url,
            )
        )
    output = str(payload.get("output") or "").strip() or None
    return ProbeConfig(targets=tuple(targets), output=output)


def _profiles(target: TargetConfig) -> tuple[str, ...]:
    if "auto" in target.profiles:
        return ("common", "openai", "ollama", "nexus_runtime", "vector", "voice")
    values = list(target.profiles)
    if "common" not in values:
        values.insert(0, "common")
    return tuple(dict.fromkeys(values))


def _passive_requests(target: TargetConfig) -> list[tuple[str, str, str]]:
    output: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for profile in _profiles(target):
        for method, path in PASSIVE_PATHS.get(profile, ()):
            key = (method, path)
            if key not in seen:
                output.append((profile, method, path))
                seen.add(key)
    for name, endpoint in target.endpoints.items():
        if name == "rag_upsert_declared":
            continue
        path = urllib.parse.urlsplit(endpoint).path
        key = ("GET", path)
        if key not in seen:
            output.append(("declared", "GET", path))
            seen.add(key)
    return output


def _response_status(response: HttpResponse) -> str:
    if response.error_code and response.status is None:
        return response.error_code
    if response.status is None:
        return "unknown_error"
    if 200 <= response.status < 300:
        return "available"
    if response.status in {401, 403}:
        return "auth_required"
    if response.status == 404:
        return "not_found"
    if response.status == 405:
        return "method_not_allowed"
    if response.status in {400, 409, 415, 422}:
        return "endpoint_present_request_invalid"
    if response.status == 429:
        return "rate_limited"
    if 500 <= response.status < 600:
        return "server_error"
    return "http_error"


def _content_type(headers: Mapping[str, str]) -> str:
    for key, value in headers.items():
        if key.lower() == "content-type":
            return value.split(";", 1)[0].strip().lower()
    return ""


def _body_digest(body: bytes) -> str | None:
    return "sha256:" + hashlib.sha256(body).hexdigest() if body else None


def _safe_string(value: Any, *, maximum: int = 200) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.strip().split())
    if not cleaned or len(cleaned) > maximum or any(ord(ch) < 32 for ch in cleaned):
        return None
    return cleaned


def _safe_model_name(value: Any) -> str | None:
    cleaned = _safe_string(value, maximum=200)
    if not cleaned or any(token in cleaned.lower() for token in ("bearer ", "api_key=", "token=")):
        return None
    return cleaned


def _extract_models(payload: Any) -> list[str]:
    candidates: list[Any] = []
    if isinstance(payload, dict):
        for key in ("data", "models"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend(value)
        if isinstance(payload.get("model"), str):
            candidates.append(payload.get("model"))
    elif isinstance(payload, list):
        candidates.extend(payload)
    names: list[str] = []
    for item in candidates:
        value: Any = item
        if isinstance(item, dict):
            value = item.get("id") or item.get("name") or item.get("model") or item.get("model_name")
        name = _safe_model_name(value)
        if name and name not in names:
            names.append(name)
        if len(names) >= MAX_MODELS:
            break
    return names


def _model_categories(models: Iterable[str]) -> dict[str, list[str]]:
    output = {"llm": [], "embedding": [], "stt": [], "tts": [], "reranker": [], "unknown": []}
    for model in models:
        lowered = model.lower()
        if any(token in lowered for token in ("embed", "bge", "e5-", "gte-", "nomic-embed")):
            bucket = "embedding"
        elif any(token in lowered for token in ("whisper", "stt", "speech-to-text", "asr")):
            bucket = "stt"
        elif any(token in lowered for token in ("tts", "kokoro", "piper", "cosyvoice", "fish-speech", "speech-synthesis")):
            bucket = "tts"
        elif any(token in lowered for token in ("rerank", "cross-encoder")):
            bucket = "reranker"
        elif any(token in lowered for token in ("llama", "qwen", "mistral", "gemma", "phi", "deepseek", "gpt", "command", "yi", "chat")):
            bucket = "llm"
        else:
            bucket = "unknown"
        output[bucket].append(model)
    return output


def _safe_json_summary(payload: Any, *, path: str, content_type: str) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    if isinstance(payload, dict):
        summary["top_level_keys"] = sorted(str(key)[:80] for key in payload.keys())[:MAX_SAFE_KEYS]
        models = _extract_models(payload)
        if models:
            summary["models"] = models
        for key in ("version", "status", "state", "object", "model", "language"):
            value = _safe_string(payload.get(key), maximum=120)
            if value:
                summary[key] = value
        for key in ("count", "total", "collections_count"):
            value = payload.get(key)
            if isinstance(value, int) and 0 <= value <= 10_000_000:
                summary[key] = value
        data = payload.get("data")
        if isinstance(data, list):
            summary["data_count"] = len(data)
            if data and isinstance(data[0], dict):
                vector = data[0].get("embedding")
                if isinstance(vector, list):
                    summary["embedding_dimension"] = len(vector)
        collections = payload.get("result")
        if isinstance(collections, dict) and isinstance(collections.get("collections"), list):
            names = []
            for item in collections["collections"][:200]:
                if isinstance(item, dict):
                    name = _safe_string(item.get("name"), maximum=200)
                    if name:
                        names.append(hashlib.sha256(name.encode()).hexdigest()[:16])
            summary["collection_count"] = len(collections["collections"])
            summary["collection_name_fingerprints"] = names[:50]
        if path.endswith("/v1/schema") and isinstance(payload.get("classes"), list):
            summary["schema_class_count"] = len(payload["classes"])
    elif isinstance(payload, list):
        summary["list_count"] = len(payload)
        models = _extract_models(payload)
        if models:
            summary["models"] = models
    if content_type:
        summary["content_type"] = content_type
    return summary


def _parse_json(body: bytes) -> Any | None:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return None


def summarize_response(response: HttpResponse) -> dict[str, Any]:
    parsed = urllib.parse.urlsplit(response.url)
    content_type = _content_type(response.headers)
    payload = _parse_json(response.body)
    result: dict[str, Any] = {
        "method": response.method,
        "path": parsed.path or "/",
        "status": _response_status(response),
        "http_status": response.status,
        "latency_ms": response.latency_ms,
        "content_type": content_type or None,
        "response_bytes": len(response.body),
        "response_sha256": _body_digest(response.body),
        "error_code": response.error_code,
    }
    safe_summary = _safe_json_summary(payload, path=parsed.path, content_type=content_type) if payload is not None else {}
    if response.body and payload is None:
        safe_summary["binary_or_text_body"] = True
    result["safe_summary"] = safe_summary
    return result


def _json_body(payload: Mapping[str, Any]) -> tuple[bytes, dict[str, str]]:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), {"Content-Type": "application/json"}


def _multipart(fields: Mapping[str, str], file_field: str, filename: str, file_bytes: bytes, content_type: str) -> tuple[bytes, dict[str, str]]:
    boundary = "----nexus-ai-probe-" + uuid.uuid4().hex
    buffer = io.BytesIO()
    for key, value in fields.items():
        buffer.write(f"--{boundary}\r\n".encode())
        buffer.write(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        buffer.write(value.encode("utf-8"))
        buffer.write(b"\r\n")
    buffer.write(f"--{boundary}\r\n".encode())
    buffer.write(f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode())
    buffer.write(f"Content-Type: {content_type}\r\n\r\n".encode())
    buffer.write(file_bytes)
    buffer.write(b"\r\n")
    buffer.write(f"--{boundary}--\r\n".encode())
    return buffer.getvalue(), {"Content-Type": f"multipart/form-data; boundary={boundary}"}


def _wav_metadata(path: str) -> tuple[bytes, dict[str, int]]:
    file_path = Path(path)
    if not file_path.is_file() or file_path.is_symlink() or file_path.stat().st_size > 10 * 1024 * 1024:
        raise ProbeConfigError("stt_sample_file_invalid")
    raw = file_path.read_bytes()
    with wave.open(io.BytesIO(raw), "rb") as wav:
        metadata = {
            "channels": wav.getnchannels(),
            "sample_rate": wav.getframerate(),
            "sample_width": wav.getsampwidth(),
            "frames": wav.getnframes(),
        }
    return raw, metadata


def _active_specs(target: TargetConfig, observed: Mapping[str, Any]) -> list[str]:
    if target.mode != "active":
        return []
    if "auto" not in target.active_tests:
        return list(dict.fromkeys(target.active_tests))[: target.max_active_calls]
    paths = {item.get("path"): item.get("status") for item in observed.get("endpoints", []) if isinstance(item, dict)}
    tests: list[str] = []
    if paths.get("/v1/chat/completions") not in {None, "not_found"}:
        tests.append("openai_chat")
    if paths.get("/v1/responses") not in {None, "not_found"}:
        tests.append("openai_responses")
    if paths.get("/v1/embeddings") not in {None, "not_found"}:
        tests.append("openai_embeddings")
    if paths.get("/api/chat") not in {None, "not_found"}:
        tests.append("ollama_chat")
    if paths.get("/api/embed") not in {None, "not_found"} or paths.get("/api/embeddings") not in {None, "not_found"}:
        tests.append("ollama_embeddings")
    if "nexus_llm" in target.endpoints:
        tests.append("nexus_llm_bridge")
    if "nexus_rag" in target.endpoints:
        tests.append("nexus_rag_question")
    if "nexus_tts" in target.endpoints:
        tests.append("nexus_tts_bridge")
    if "openai_tts" in target.endpoints or paths.get("/v1/audio/speech") not in {None, "not_found"}:
        tests.append("openai_tts")
    if target.stt_sample_file:
        if "nexus_stt" in target.endpoints:
            tests.append("nexus_stt_bridge")
        if "openai_stt" in target.endpoints or paths.get("/v1/audio/transcriptions") not in {None, "not_found"}:
            tests.append("openai_stt")
    return list(dict.fromkeys(tests))[: target.max_active_calls]


def _selected_model(target: TargetConfig, category: str, observed_models: Mapping[str, list[str]]) -> str | None:
    explicit = target.models.get(category)
    if explicit:
        return explicit
    candidates = observed_models.get(category) or []
    if candidates:
        return candidates[0]
    if category == "llm":
        unknown = observed_models.get("unknown") or []
        return unknown[0] if unknown else None
    return None


def _active_request(
    *,
    target: TargetConfig,
    transport: Transport,
    test_name: str,
    observed_models: Mapping[str, list[str]],
) -> dict[str, Any]:
    endpoint: str
    model: str | None
    body: bytes
    headers: dict[str, str]
    prompt_sha = None
    if test_name == "openai_chat":
        endpoint = target.endpoints.get("openai_chat") or _join_target_url(target.base_url, "/v1/chat/completions")
        model = _selected_model(target, "llm", observed_models)
        if not model:
            return {"test": test_name, "status": "skipped", "reason": "llm_model_unknown"}
        body, headers = _json_body({"model": model, "messages": [{"role": "user", "content": FIXED_LLM_PROMPT}], "temperature": 0, "max_tokens": 32, "stream": False})
        prompt_sha = hashlib.sha256(FIXED_LLM_PROMPT.encode()).hexdigest()
    elif test_name == "openai_responses":
        endpoint = target.endpoints.get("openai_responses") or _join_target_url(target.base_url, "/v1/responses")
        model = _selected_model(target, "llm", observed_models)
        if not model:
            return {"test": test_name, "status": "skipped", "reason": "llm_model_unknown"}
        body, headers = _json_body({"model": model, "input": FIXED_LLM_PROMPT, "max_output_tokens": 32, "temperature": 0})
        prompt_sha = hashlib.sha256(FIXED_LLM_PROMPT.encode()).hexdigest()
    elif test_name == "openai_embeddings":
        endpoint = target.endpoints.get("openai_embeddings") or _join_target_url(target.base_url, "/v1/embeddings")
        model = _selected_model(target, "embedding", observed_models)
        if not model:
            return {"test": test_name, "status": "skipped", "reason": "embedding_model_unknown"}
        payload: dict[str, Any] = {"model": model, "input": [FIXED_EMBEDDING_TEXT]}
        dimension_hint = target.models.get("embedding_dimension")
        if dimension_hint and str(dimension_hint).isdigit():
            payload["dimensions"] = int(dimension_hint)
        body, headers = _json_body(payload)
        prompt_sha = hashlib.sha256(FIXED_EMBEDDING_TEXT.encode()).hexdigest()
    elif test_name == "ollama_chat":
        endpoint = target.endpoints.get("ollama_chat") or _join_target_url(target.base_url, "/api/chat")
        model = _selected_model(target, "llm", observed_models)
        if not model:
            return {"test": test_name, "status": "skipped", "reason": "llm_model_unknown"}
        body, headers = _json_body({"model": model, "messages": [{"role": "user", "content": FIXED_LLM_PROMPT}], "stream": False, "format": "json", "options": {"temperature": 0, "num_predict": 32}})
        prompt_sha = hashlib.sha256(FIXED_LLM_PROMPT.encode()).hexdigest()
    elif test_name == "ollama_embeddings":
        path = "/api/embed"
        endpoint = target.endpoints.get("ollama_embeddings") or _join_target_url(target.base_url, path)
        model = _selected_model(target, "embedding", observed_models)
        if not model:
            return {"test": test_name, "status": "skipped", "reason": "embedding_model_unknown"}
        body, headers = _json_body({"model": model, "input": [FIXED_EMBEDDING_TEXT]})
        prompt_sha = hashlib.sha256(FIXED_EMBEDDING_TEXT.encode()).hexdigest()
    elif test_name == "nexus_llm_bridge":
        endpoint = target.endpoints.get("nexus_llm") or ""
        if not endpoint:
            return {"test": test_name, "status": "skipped", "reason": "nexus_llm_endpoint_missing"}
        model = _selected_model(target, "llm", observed_models)
        payload = {"system": "Return bounded JSON only.", "input": FIXED_LLM_PROMPT, "language": "en", "response_format": "json"}
        if model:
            payload["model"] = model
        body, headers = _json_body(payload)
        prompt_sha = hashlib.sha256(FIXED_LLM_PROMPT.encode()).hexdigest()
    elif test_name == "nexus_rag_question":
        endpoint = target.endpoints.get("nexus_rag") or ""
        if not endpoint:
            return {"test": test_name, "status": "skipped", "reason": "nexus_rag_endpoint_missing"}
        model = _selected_model(target, "llm", observed_models)
        payload = {"question": FIXED_RAG_QUESTION}
        if model:
            payload["model"] = model
        body, headers = _json_body(payload)
        prompt_sha = hashlib.sha256(FIXED_RAG_QUESTION.encode()).hexdigest()
    elif test_name in {"openai_tts", "nexus_tts_bridge"}:
        endpoint = target.endpoints.get("nexus_tts") if test_name == "nexus_tts_bridge" else target.endpoints.get("openai_tts")
        if test_name == "nexus_tts_bridge" and not endpoint:
            return {"test": test_name, "status": "skipped", "reason": "nexus_tts_endpoint_missing"}
        endpoint = endpoint or _join_target_url(target.base_url, "/v1/audio/speech")
        model = target.models.get("tts") or _selected_model(target, "tts", observed_models)
        voice = target.models.get("tts_voice") or "alloy"
        if test_name == "openai_tts":
            if not model:
                return {"test": test_name, "status": "skipped", "reason": "tts_model_unknown"}
            payload = {"model": model, "input": FIXED_TTS_TEXT, "voice": voice, "response_format": "wav"}
        else:
            payload = {"text": FIXED_TTS_TEXT, "language": "en", "voice": voice, "format": "wav"}
            if model:
                payload["model"] = model
        body, headers = _json_body(payload)
        prompt_sha = hashlib.sha256(FIXED_TTS_TEXT.encode()).hexdigest()
    elif test_name in {"openai_stt", "nexus_stt_bridge"}:
        if not target.stt_sample_file:
            return {"test": test_name, "status": "skipped", "reason": "stt_sample_file_missing"}
        audio, meta = _wav_metadata(target.stt_sample_file)
        model = target.models.get("stt") or _selected_model(target, "stt", observed_models)
        if test_name == "openai_stt":
            if not model:
                return {"test": test_name, "status": "skipped", "reason": "stt_model_unknown"}
            endpoint = target.endpoints.get("openai_stt") or _join_target_url(target.base_url, "/v1/audio/transcriptions")
            body, headers = _multipart({"model": model, "language": "en"}, "file", "probe.wav", audio, "audio/wav")
        else:
            endpoint = target.endpoints.get("nexus_stt") or ""
            if not endpoint:
                return {"test": test_name, "status": "skipped", "reason": "nexus_stt_endpoint_missing"}
            fields = {"language": "en", "sample_rate": str(meta["sample_rate"]), "channels": str(meta["channels"])}
            if model:
                fields["model"] = model
            body, headers = _multipart(fields, "audio", "probe.wav", audio, "audio/wav")
        prompt_sha = hashlib.sha256(audio).hexdigest()
    else:
        return {"test": test_name, "status": "skipped", "reason": "unsupported_active_test"}

    response = transport.request(target=target, method="POST", url=endpoint, headers=headers, body=body)
    summary = summarize_response(response)
    payload = _parse_json(response.body)
    active_summary: dict[str, Any] = {
        "test": test_name,
        "status": summary["status"],
        "path": summary["path"],
        "http_status": summary["http_status"],
        "latency_ms": summary["latency_ms"],
        "response_bytes": summary["response_bytes"],
        "response_sha256": summary["response_sha256"],
        "content_type": summary["content_type"],
        "model": model,
        "synthetic_input_sha256": prompt_sha,
        "safe_response": summary["safe_summary"],
        "dimension_requested": bool(test_name == "openai_embeddings" and target.models.get("embedding_dimension")),
    }
    if test_name in {"openai_tts", "nexus_tts_bridge"} and response.body:
        active_summary["audio_detected"] = _looks_like_audio(response.body, summary["content_type"] or "")
    if test_name in {"openai_stt", "nexus_stt_bridge"} and isinstance(payload, dict):
        text = payload.get("text") or payload.get("transcript")
        if isinstance(text, str):
            active_summary["transcript_chars"] = len(text)
            active_summary["transcript_sha256"] = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        confidence = payload.get("confidence")
        if isinstance(confidence, (int, float)) and math.isfinite(float(confidence)):
            active_summary["confidence"] = round(float(confidence), 4)
    return active_summary


def _looks_like_audio(body: bytes, content_type: str) -> bool:
    if content_type.startswith("audio/") or content_type == "application/octet-stream":
        return len(body) > 44
    return body.startswith(b"RIFF") and body[8:12] == b"WAVE"


def probe_websocket(url: str, *, timeout_seconds: int, verify_tls: bool) -> dict[str, Any]:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    started = time.monotonic()
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    try:
        raw_socket = socket.create_connection((host, port), timeout=timeout_seconds)
        sock = raw_socket
        if parsed.scheme == "wss":
            context = ssl.create_default_context() if verify_tls else ssl._create_unverified_context()  # noqa: SLF001
            sock = context.wrap_socket(raw_socket, server_hostname=host)
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        sock.sendall(request)
        data = sock.recv(4096)
        sock.close()
        status_line = data.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
        match = re.match(r"HTTP/\d(?:\.\d)?\s+(\d{3})", status_line)
        status = int(match.group(1)) if match else None
        if status == 101:
            verdict = "available"
        elif status in {401, 403}:
            verdict = "auth_required"
        elif status == 404:
            verdict = "not_found"
        else:
            verdict = "handshake_rejected" if status else "invalid_response"
        return {"url": _safe_ws_url(url), "status": verdict, "http_status": status, "latency_ms": _elapsed_ms(started)}
    except socket.timeout:
        return {"url": _safe_ws_url(url), "status": "timeout", "http_status": None, "latency_ms": _elapsed_ms(started)}
    except ssl.SSLError:
        return {"url": _safe_ws_url(url), "status": "tls_error", "http_status": None, "latency_ms": _elapsed_ms(started)}
    except OSError as exc:
        return {"url": _safe_ws_url(url), "status": f"network_{exc.__class__.__name__}", "http_status": None, "latency_ms": _elapsed_ms(started)}


def _safe_ws_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return urllib.parse.urlunsplit((parsed.scheme, f"{host}{port}", parsed.path or "/", "", ""))


def _nexus_mapping(target: TargetConfig, report: Mapping[str, Any]) -> dict[str, Any]:
    endpoints = {item.get("path"): item for item in report.get("endpoints", []) if isinstance(item, dict)}
    active = {item.get("test"): item for item in report.get("active_tests", []) if isinstance(item, dict)}
    models = report.get("model_categories") if isinstance(report.get("model_categories"), dict) else {}
    recommendations: dict[str, Any] = {
        "provider_runtime": {"fit": "unknown", "environment": {}, "warnings": []},
        "knowledge_embeddings": {"fit": "unknown", "environment": {}, "warnings": []},
        "rag": {"fit": "unknown", "environment": {}, "warnings": []},
        "voice": {"fit": "unknown", "environment": {}, "warnings": []},
    }

    if _active_pass(active.get("ollama_chat")) or _present(endpoints.get("/api/chat")):
        recommendations["provider_runtime"] = {
            "fit": "direct",
            "environment": {
                "PRIVATE_AI_RUNTIME_BASE_URL": target.base_url,
                "PRIVATE_AI_RUNTIME_DIRECT_PATH": "/api/chat",
                "PRIVATE_AI_RUNTIME_REQUEST_SHAPE": "ollama_chat",
                "PRIVATE_AI_RUNTIME_DIRECT_MODEL": _first_model(models, "llm"),
            },
            "warnings": [],
        }
    elif _active_pass(active.get("nexus_llm_bridge")):
        path = urllib.parse.urlsplit(target.endpoints["nexus_llm"]).path
        recommendations["provider_runtime"] = {
            "fit": "direct",
            "environment": {
                "PRIVATE_AI_RUNTIME_BASE_URL": target.base_url,
                "PRIVATE_AI_RUNTIME_DIRECT_PATH": path,
                "PRIVATE_AI_RUNTIME_REQUEST_SHAPE": "system_input",
                "PRIVATE_AI_RUNTIME_DIRECT_MODEL": _first_model(models, "llm"),
            },
            "warnings": [],
        }
    elif _active_pass(active.get("openai_chat")) or _present(endpoints.get("/v1/chat/completions")):
        recommendations["provider_runtime"] = {
            "fit": "adapter_review_required",
            "environment": {
                "PRIVATE_AI_RUNTIME_BASE_URL": target.base_url,
                "PRIVATE_AI_RUNTIME_DIRECT_PATH": "/v1/chat/completions",
                "PRIVATE_AI_RUNTIME_REQUEST_SHAPE": "messages",
                "PRIVATE_AI_RUNTIME_DIRECT_MODEL": _first_model(models, "llm"),
            },
            "warnings": ["Nexus messages shape currently emits response_format as a string; verify the target accepts that contract before enabling traffic."],
        }
    elif _active_pass(active.get("openai_responses")) or _present(endpoints.get("/v1/responses")):
        recommendations["provider_runtime"]["fit"] = "adapter_required"
        recommendations["provider_runtime"]["warnings"] = ["OpenAI Responses is available, but the current Nexus private runtime adapter does not emit the canonical Responses payload."]

    embedding = active.get("openai_embeddings")
    if _active_pass(embedding):
        dim = _nested(embedding, "safe_response", "embedding_dimension")
        base = target.base_url.rstrip("/")
        if not urllib.parse.urlsplit(base).path.rstrip("/").endswith("/v1"):
            base = base + "/v1"
        recommendations["knowledge_embeddings"] = {
            "fit": "direct",
            "environment": {
                "KNOWLEDGE_EMBEDDING_PROVIDER": "openai_compatible",
                "KNOWLEDGE_EMBEDDING_BASE_URL": base,
                "KNOWLEDGE_EMBEDDING_MODEL": _first_model(models, "embedding"),
                "KNOWLEDGE_EMBEDDING_DIM": dim,
                "KNOWLEDGE_EMBEDDING_DIMENSION_REQUEST_SUPPORTED": bool(embedding.get("dimension_requested")),
            },
            "warnings": [],
        }
    elif _present(endpoints.get("/v1/embeddings")):
        recommendations["knowledge_embeddings"]["fit"] = "active_probe_required"

    if _active_pass(active.get("nexus_rag_question")):
        path = urllib.parse.urlsplit(target.endpoints["nexus_rag"]).path
        recommendations["rag"] = {
            "fit": "direct_query",
            "environment": {
                "PRIVATE_AI_RUNTIME_RAG_BASE_URL": target.base_url,
                "PRIVATE_AI_RUNTIME_RAG_PATH": path,
                "PRIVATE_AI_RUNTIME_REQUEST_SHAPE": "question",
                "PRIVATE_AI_RUNTIME_RAG_MODEL": _first_model(models, "llm"),
            },
            "warnings": [],
        }
    elif _active_pass(active.get("ollama_chat")) and "rag_upsert_declared" in target.endpoints:
        recommendations["rag"] = {
            "fit": "query_direct_ingestion_declared_not_tested",
            "environment": {
                "PRIVATE_AI_RUNTIME_RAG_BASE_URL": target.base_url,
                "PRIVATE_AI_RUNTIME_RAG_PATH": "/api/chat",
                "PRIVATE_AI_RUNTIME_REQUEST_SHAPE": "ollama_chat",
                "AI_RUNTIME_RAG_UPSERT_PATH": urllib.parse.urlsplit(target.endpoints["rag_upsert_declared"]).path,
            },
            "warnings": ["The probe never invokes RAG upsert. Run the existing Nexus dry-run sync before any authorized ingestion."],
        }

    voice_fit: list[str] = []
    voice_env: dict[str, Any] = {}
    voice_warnings: list[str] = []
    if _active_pass(active.get("nexus_stt_bridge")):
        voice_fit.append("stt_direct")
        voice_env.update({"STT_PROVIDER": "external", "STT_ENDPOINT": target.endpoints["nexus_stt"]})
    elif _active_pass(active.get("openai_stt")):
        voice_fit.append("stt_adapter_required")
        voice_warnings.append("OpenAI STT uses multipart field `file`; Nexus WebCall bridge currently sends field `audio` with sample metadata.")
    if _active_pass(active.get("nexus_tts_bridge")):
        voice_fit.append("tts_direct")
        voice_env.update({"TTS_PROVIDER": "external", "TTS_ENDPOINT": target.endpoints["nexus_tts"]})
    elif _active_pass(active.get("openai_tts")):
        voice_fit.append("tts_adapter_review_required")
        voice_warnings.append("Verify the configured WebCall bridge accepts the OpenAI audio/speech request and response contract.")
    if _active_pass(active.get("nexus_llm_bridge")):
        voice_fit.append("llm_direct")
        voice_env.update({"LLM_PROVIDER": "external", "LLM_ENDPOINT": target.endpoints["nexus_llm"]})
    if target.websocket_url:
        voice_env["LIVE_VOICE_UPSTREAM_WS_URL"] = _safe_ws_url(target.websocket_url)
    if "voice_health" in target.endpoints:
        voice_env["LIVE_VOICE_UPSTREAM_HEALTH_URL"] = target.endpoints["voice_health"]
    if voice_fit or voice_env:
        recommendations["voice"] = {"fit": "+".join(voice_fit) if voice_fit else "health_only", "environment": voice_env, "warnings": voice_warnings}
    return recommendations


def _active_pass(item: Any) -> bool:
    return isinstance(item, dict) and item.get("status") == "available"


def _present(item: Any) -> bool:
    return isinstance(item, dict) and item.get("status") in {"available", "auth_required", "method_not_allowed", "endpoint_present_request_invalid"}


def _first_model(models: Any, key: str) -> str | None:
    if isinstance(models, dict) and isinstance(models.get(key), list) and models[key]:
        return str(models[key][0])
    return None


def _nested(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _capability_inventory(report: Mapping[str, Any]) -> dict[str, Any]:
    endpoints = {item.get("path"): item for item in report.get("endpoints", []) if isinstance(item, dict)}
    active = {item.get("test"): item for item in report.get("active_tests", []) if isinstance(item, dict)}
    models = report.get("model_categories") if isinstance(report.get("model_categories"), dict) else {}

    def verdict(path: str, active_name: str | None = None) -> str:
        if active_name and isinstance(active.get(active_name), dict):
            status = str(active[active_name].get("status") or "unknown")
            if status == "available":
                return "verified"
            if status not in {"skipped", "unknown"}:
                return status
        endpoint = endpoints.get(path)
        return str(endpoint.get("status") or "unknown") if isinstance(endpoint, dict) else "unknown"

    vector_hints: list[str] = []
    if _present(endpoints.get("/collections")):
        vector_hints.append("qdrant_or_compatible")
    if _present(endpoints.get("/v1/meta")) or _present(endpoints.get("/v1/schema")):
        vector_hints.append("weaviate_or_compatible")
    if _present(endpoints.get("/api/v2/heartbeat")) or _present(endpoints.get("/api/v1/heartbeat")):
        vector_hints.append("chroma_or_compatible")
    return {
        "llm": {
            "model_count": len(models.get("llm", [])) if isinstance(models, dict) else 0,
            "openai_chat": verdict("/v1/chat/completions", "openai_chat"),
            "openai_responses": verdict("/v1/responses", "openai_responses"),
            "ollama_chat": verdict("/api/chat", "ollama_chat"),
            "nexus_bridge": str(active.get("nexus_llm_bridge", {}).get("status") or "unknown") if isinstance(active.get("nexus_llm_bridge"), dict) else "unknown",
        },
        "embeddings": {
            "model_count": len(models.get("embedding", [])) if isinstance(models, dict) else 0,
            "openai_compatible": verdict("/v1/embeddings", "openai_embeddings"),
            "ollama": verdict("/api/embed", "ollama_embeddings"),
            "dimension": _nested(active.get("openai_embeddings"), "safe_response", "embedding_dimension"),
        },
        "rag": {
            "query": str(active.get("nexus_rag_question", {}).get("status") or "unknown") if isinstance(active.get("nexus_rag_question"), dict) else "unknown",
            "health": verdict("/rag/health"),
            "upsert_declared_not_called": bool(report.get("declared_write_endpoints_not_called")),
            "vector_store_hints": vector_hints,
        },
        "voice": {
            "stt_models": len(models.get("stt", [])) if isinstance(models, dict) else 0,
            "tts_models": len(models.get("tts", [])) if isinstance(models, dict) else 0,
            "openai_stt": verdict("/v1/audio/transcriptions", "openai_stt"),
            "openai_tts": verdict("/v1/audio/speech", "openai_tts"),
            "nexus_stt_bridge": str(active.get("nexus_stt_bridge", {}).get("status") or "unknown") if isinstance(active.get("nexus_stt_bridge"), dict) else "unknown",
            "nexus_tts_bridge": str(active.get("nexus_tts_bridge", {}).get("status") or "unknown") if isinstance(active.get("nexus_tts_bridge"), dict) else "unknown",
            "websocket": _nested(report.get("websocket"), "status"),
        },
        "reranker": {"model_count": len(models.get("reranker", [])) if isinstance(models, dict) else 0},
    }


def probe_target(target: TargetConfig, *, transport: Transport | None = None) -> dict[str, Any]:
    transport = transport or UrllibTransport()
    endpoints: list[dict[str, Any]] = []
    all_models: list[str] = []
    inference_calls = 0
    for profile, method, path in _passive_requests(target):
        url = _join_target_url(target.base_url, path)
        response = transport.request(target=target, method=method, url=url)
        summary = summarize_response(response)
        summary["profile"] = profile
        endpoints.append(summary)
        for model in summary.get("safe_summary", {}).get("models", []):
            if model not in all_models:
                all_models.append(model)
    categories = _model_categories(all_models)
    partial: dict[str, Any] = {
        "name": target.name,
        "base_url": target.base_url,
        "profiles": list(_profiles(target)),
        "mode": target.mode,
        "verify_tls": target.verify_tls,
        "auth": {
            "type": target.auth.kind,
            "source_configured": bool(target.auth.env or target.auth.file),
            "credential_available": bool(target.auth.resolve()[1]),
        },
        "endpoints": endpoints,
        "models": all_models,
        "model_categories": categories,
        "declared_write_endpoints_not_called": [
            {"name": name, "path": urllib.parse.urlsplit(url).path}
            for name, url in target.endpoints.items()
            if name == "rag_upsert_declared"
        ],
        "active_tests": [],
        "websocket": None,
        "side_effects": {"inference_calls": 0, "write_calls": 0, "audio_persisted": False, "raw_bodies_persisted": False},
    }
    for test_name in _active_specs(target, partial):
        try:
            result = _active_request(target=target, transport=transport, test_name=test_name, observed_models=categories)
        except ProbeConfigError as exc:
            result = {"test": test_name, "status": "probe_error", "reason": str(exc)[:80]}
        except Exception as exc:  # defensive target isolation; never retain provider payloads
            result = {"test": test_name, "status": "probe_error", "reason": exc.__class__.__name__}
        partial["active_tests"].append(result)
        if result.get("status") not in {"skipped", "probe_error"}:
            inference_calls += 1
    if target.websocket_url:
        partial["websocket"] = probe_websocket(target.websocket_url, timeout_seconds=target.timeout_seconds, verify_tls=target.verify_tls)
    partial["side_effects"]["inference_calls"] = inference_calls
    partial["capabilities"] = _capability_inventory(partial)
    partial["nexus_compatibility"] = _nexus_mapping(target, partial)
    reachable = any(item.get("status") not in {"not_found", "unreachable", "timeout", "tls_error"} for item in endpoints)
    partial["reachable"] = reachable
    return partial


def build_report(config: ProbeConfig, *, transport: Transport | None = None) -> dict[str, Any]:
    started = time.monotonic()
    targets = [probe_target(target, transport=transport) for target in config.targets]
    capability_counts = {
        "targets": len(targets),
        "reachable_targets": sum(1 for item in targets if item.get("reachable")),
        "models": sum(len(item.get("models", [])) for item in targets),
        "active_tests": sum(len(item.get("active_tests", [])) for item in targets),
        "active_tests_available": sum(
            1
            for item in targets
            for test in item.get("active_tests", [])
            if isinstance(test, dict) and test.get("status") == "available"
        ),
    }
    return {
        "schema": REPORT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": _elapsed_ms(started),
        "mode": sorted({target.mode for target in config.targets}),
        "safety": {
            "declared_urls_only": True,
            "port_scan": False,
            "endpoint_crawling": False,
            "write_requests": False,
            "rag_ingestion": False,
            "tool_execution": False,
            "outbound_send": False,
            "raw_provider_bodies_retained": False,
            "credentials_retained": False,
            "tls_verification_default": True,
            "active_inference_requires_explicit_mode": True,
        },
        "summary": capability_counts,
        "targets": targets,
    }


def _write_report(path: Path, report: Mapping[str, Any], *, pretty: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2 if pretty else None, separators=None if pretty else (",", ":")) + "\n"
    path.write_text(encoded, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _cli_config(args: argparse.Namespace) -> ProbeConfig:
    if args.config:
        config = load_config(Path(args.config))
        if args.mode:
            targets = tuple(TargetConfig(**{**target.__dict__, "mode": args.mode}) for target in config.targets)
            return ProbeConfig(targets=targets, output=config.output)
        return config
    if not args.base_url:
        raise ProbeConfigError("config_or_base_url_required")
    active_tests = tuple(item.strip() for item in (args.active_tests or "").split(",") if item.strip())
    payload = {
        "schema": CONFIG_SCHEMA,
        "mode": args.mode or "passive",
        "verify_tls": not args.insecure,
        "targets": [
            {
                "name": args.name or "ai-runtime",
                "base_url": args.base_url,
                "profiles": [item.strip() for item in (args.profiles or "auto").split(",") if item.strip()],
                "auth": {
                    "type": "bearer" if (args.token_env or args.token_file) else "none",
                    "env": args.token_env,
                    "file": args.token_file,
                },
                "active_tests": list(active_tests),
                "stt_sample_file": args.stt_sample,
                "websocket_url": args.websocket_url,
            }
        ],
    }
    return parse_config(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only full AI resource server capability probe for Nexus OSR")
    parser.add_argument("--config", help="Path to nexus.ai_resource_probe.config.v1 JSON")
    parser.add_argument("--base-url", help="Convenience single-target base URL")
    parser.add_argument("--name", help="Convenience target name")
    parser.add_argument("--profiles", default="auto", help="Comma-separated profiles for single-target mode")
    parser.add_argument("--mode", choices=("passive", "active"), help="Override probe mode")
    parser.add_argument("--active-tests", help="Comma-separated explicit active tests for single-target mode")
    parser.add_argument("--token-env", help="Environment variable containing a bearer token")
    parser.add_argument("--token-file", help="File containing a bearer token")
    parser.add_argument("--stt-sample", help="Explicit synthetic WAV used only for active STT tests")
    parser.add_argument("--websocket-url", help="Explicit ws:// or wss:// endpoint for handshake-only probing")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification for explicitly declared lab targets")
    parser.add_argument("--output", default="artifacts/ai-resource-probe.json")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--fail-on-unreachable", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = _cli_config(args)
        report = build_report(config)
        output = Path(args.output or config.output or "artifacts/ai-resource-probe.json")
        _write_report(output, report, pretty=args.pretty)
    except ProbeConfigError as exc:
        print(f"AI_RESOURCE_PROBE_CONFIG_ERROR={exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    print(f"AI_RESOURCE_PROBE_WRITTEN={output}")
    print(f"AI_RESOURCE_PROBE_REACHABLE_TARGETS={report['summary']['reachable_targets']}")
    if args.fail_on_unreachable and report["summary"]["reachable_targets"] != report["summary"]["targets"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
