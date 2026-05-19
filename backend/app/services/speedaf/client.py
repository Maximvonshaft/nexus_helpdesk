from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx

from .redactor import redact_mapping
from .schemas import SpeedafMcpConfig, SpeedafMcpEnvelope, SpeedafMcpNormalizedError


class SpeedafMcpClientError(RuntimeError):
    def __init__(self, error: SpeedafMcpNormalizedError, *, safe_payload: dict[str, Any] | None = None) -> None:
        self.error = error
        self.safe_payload = safe_payload or {}
        super().__init__(f"speedaf_mcp_error:{error.code}")


@dataclass(frozen=True)
class SpeedafMcpResponse:
    ok: bool
    data: Any
    raw: dict[str, Any]
    status_code: int
    safe_summary: dict[str, Any]
    error: SpeedafMcpNormalizedError | None = None


_ALLOWED_CONTENT_TYPES = {"text/plain", "application/json"}
_ALLOWED_DATA_MODES = {"string", "object"}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 30) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def load_speedaf_mcp_config() -> SpeedafMcpConfig:
    content_type = (os.getenv("SPEEDAF_MCP_CONTENT_TYPE", "text/plain").strip().lower() or "text/plain")
    if content_type not in _ALLOWED_CONTENT_TYPES:
        content_type = "text/plain"
    data_mode = (os.getenv("SPEEDAF_MCP_DATA_MODE", "string").strip().lower() or "string")
    if data_mode not in _ALLOWED_DATA_MODES:
        data_mode = "string"
    return SpeedafMcpConfig(
        enabled=_env_bool("SPEEDAF_MCP_ENABLED", False),
        base_url=(os.getenv("SPEEDAF_MCP_BASE_URL", "https://uat-api.speedaf.com").strip() or "https://uat-api.speedaf.com").rstrip("/"),
        app_code=os.getenv("SPEEDAF_MCP_APP_CODE"),
        secret_key=os.getenv("SPEEDAF_MCP_SECRET_KEY"),
        timeout_seconds=_env_int("SPEEDAF_MCP_TIMEOUT_SECONDS", 8),
        country_code_default=(os.getenv("SPEEDAF_MCP_COUNTRY_CODE_DEFAULT", "CH").strip().upper() or "CH"),
        content_type=content_type,
        data_mode=data_mode,
        require_sign=_env_bool("SPEEDAF_MCP_REQUIRE_SIGN", False),
    )


class SpeedafMcpClient:
    """Minimal backend-only Speedaf MCP HTTP client.

    The client deliberately exposes only normalized responses and safe summaries.
    Secrets and raw request bodies must not be logged by callers.
    """

    def __init__(self, config: SpeedafMcpConfig | None = None, *, http_client: httpx.Client | None = None) -> None:
        self.config = config or load_speedaf_mcp_config()
        self._http_client = http_client

    def _timestamp_ms(self) -> int:
        return int(time.time() * 1000)

    def build_envelope(self, path: str, data: dict[str, Any]) -> SpeedafMcpEnvelope:
        if self.config.require_sign:
            # The source document mentions secretKey/sign errors but does not
            # define the signing algorithm. Keep this explicit rather than
            # shipping a guessed signature implementation.
            raise SpeedafMcpClientError(SpeedafMcpNormalizedError(code="sign_rule_not_configured", message="Speedaf sign rule is not configured", retryable=False))
        timestamp_ms = self._timestamp_ms()
        query = {"appCode": self.config.app_code, "timestamp": timestamp_ms}
        body_value: Any = json.dumps(data, ensure_ascii=False, separators=(",", ":")) if self.config.data_mode == "string" else data
        body = {"data": body_value}
        headers = {"Content-Type": self.config.content_type, "Accept": "application/json"}
        return SpeedafMcpEnvelope(path=path, query=query, body=body, headers=headers, timestamp_ms=timestamp_ms)

    def _url(self, path: str) -> str:
        return urljoin(self.config.base_url.rstrip("/") + "/", path.lstrip("/"))

    def post(self, path: str, data: dict[str, Any]) -> SpeedafMcpResponse:
        if not self.config.configured:
            raise SpeedafMcpClientError(SpeedafMcpNormalizedError(code="speedaf_mcp_not_configured", message="Speedaf MCP is disabled or missing appCode", retryable=False))
        envelope = self.build_envelope(path, data)
        safe_request = {
            "path": path,
            "query": {"appCode": {"redacted": True}, "timestamp": envelope.timestamp_ms},
            "body": redact_mapping(data),
            "content_type": envelope.headers.get("Content-Type"),
            "data_mode": self.config.data_mode,
        }
        try:
            client = self._http_client or httpx.Client(timeout=self.config.timeout_seconds)
            response = client.post(self._url(path), params=envelope.query, json=envelope.body, headers=envelope.headers)
        except httpx.TimeoutException as exc:
            error = SpeedafMcpNormalizedError(code="timeout", message=str(exc), retryable=True)
            raise SpeedafMcpClientError(error, safe_payload=safe_request) from exc
        except httpx.HTTPError as exc:
            error = SpeedafMcpNormalizedError(code="http_error", message=str(exc), retryable=True)
            raise SpeedafMcpClientError(error, safe_payload=safe_request) from exc
        finally:
            if self._http_client is None:
                try:
                    client.close()  # type: ignore[name-defined]
                except Exception:
                    pass

        try:
            raw = response.json()
        except ValueError:
            raw = {"raw_text": response.text[:500]}
        normalized = self.normalize_response(raw, status_code=response.status_code, safe_request=safe_request)
        if not normalized.ok and normalized.error is not None:
            raise SpeedafMcpClientError(normalized.error, safe_payload=normalized.safe_summary)
        return normalized

    def normalize_response(self, raw: Any, *, status_code: int, safe_request: dict[str, Any] | None = None) -> SpeedafMcpResponse:
        payload = raw if isinstance(raw, dict) else {"result": raw}
        success_value = payload.get("success")
        ok_value = payload.get("ok")
        code_value = payload.get("code") or payload.get("errorCode") or payload.get("error_code")
        message_value = payload.get("message") or payload.get("msg") or payload.get("errorMessage") or payload.get("error")
        http_ok = 200 <= status_code < 300
        api_ok = True
        if isinstance(success_value, bool):
            api_ok = success_value
        elif isinstance(ok_value, bool):
            api_ok = ok_value
        elif code_value not in (None, "", "0", 0, "SUCCESS", "success"):
            api_ok = False
        ok = http_ok and api_ok
        data = payload.get("data") if "data" in payload else payload.get("result", payload)
        if isinstance(data, str):
            stripped = data.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                try:
                    data = json.loads(stripped)
                except ValueError:
                    pass
        safe_summary = {
            "request": safe_request or {},
            "response": redact_mapping(payload),
            "http_status": status_code,
            "ok": ok,
        }
        error = None
        if not ok:
            error = SpeedafMcpNormalizedError(
                code=str(code_value or f"http_{status_code}"),
                message=str(message_value) if message_value is not None else None,
                retryable=status_code >= 500 or status_code in {408, 429},
                http_status=status_code,
            )
        return SpeedafMcpResponse(ok=ok, data=data, raw=payload, status_code=status_code, safe_summary=safe_summary, error=error)
