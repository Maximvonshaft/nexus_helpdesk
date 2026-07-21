from __future__ import annotations

import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from jsonschema import Draft202012Validator
from sqlalchemy.orm import Session

from .agent_control_config import INTEGRATION, ResolvedAgentConfig, resolve_published_agent_configs
from .runtime_endpoint_policy import require_http_endpoint

_CREDENTIAL_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,159}$")
_SECRET_KEYS = {"password", "secret", "token", "authorization", "api_key", "private_key", "cookie"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise urllib.error.HTTPError(newurl, code, "redirects_not_allowed", headers, fp)


@dataclass(frozen=True)
class IntegrationCallResult:
    ok: bool
    integration_key: str
    operation: str
    status: str
    result: dict[str, Any]
    error_code: str | None = None
    http_status: int | None = None

    def safe_summary(self) -> dict[str, Any]:
        return {
            "integration_key": self.integration_key,
            "operation": self.operation,
            "status": self.status,
            "result": self.result,
            "error_code": self.error_code,
            "http_status": self.http_status,
        }


def list_integration_catalog(
    db: Session,
    *,
    market_id: int | None = None,
    channel: str | None = None,
    language: str | None = None,
) -> list[dict[str, Any]]:
    rows = resolve_published_agent_configs(
        db,
        config_type=INTEGRATION,
        market_id=market_id,
        channel=channel,
        language=language,
    )
    return [
        {
            **row.safe_summary(),
            "kind": row.content.get("kind"),
            "credential_configured": bool(_credential_value(row.content.get("credential_ref"))),
            "operations": [
                {
                    "key": item.get("key"),
                    "description": item.get("description"),
                    "method": item.get("method"),
                    "risk_level": item.get("risk_level"),
                    "requires_confirmation": item.get("requires_confirmation"),
                    "enabled": item.get("enabled"),
                }
                for item in row.content.get("operations") or []
            ],
        }
        for row in rows
    ]


def execute_integration_operation(
    db: Session,
    *,
    integration_key: str,
    operation: str,
    arguments: dict[str, Any] | None,
    expected_write: bool,
    market_id: int | None = None,
    channel: str | None = None,
    language: str | None = None,
    dry_run: bool = False,
) -> IntegrationCallResult:
    integration = _resolve_integration(
        db,
        integration_key=integration_key,
        market_id=market_id,
        channel=channel,
        language=language,
    )
    operation_row = _operation(integration, operation)
    method = str(operation_row.get("method") or "GET").upper()
    is_write = method not in {"GET"}
    if is_write != bool(expected_write):
        return IntegrationCallResult(
            False,
            integration.resource_key,
            operation,
            "blocked",
            {},
            "integration_operation_classification_mismatch",
        )
    if operation_row.get("enabled") is False:
        return IntegrationCallResult(False, integration.resource_key, operation, "blocked", {}, "integration_operation_disabled")
    payload = dict(arguments or {})
    validator = Draft202012Validator(operation_row.get("input_schema") or {"type": "object"})
    errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.path))
    if errors:
        return IntegrationCallResult(
            False,
            integration.resource_key,
            operation,
            "blocked",
            {},
            "integration_arguments_invalid",
        )
    if dry_run:
        return IntegrationCallResult(
            True,
            integration.resource_key,
            operation,
            "validated",
            {
                "method": method,
                "endpoint_path": operation_row.get("path"),
                "credential_configured": bool(_credential_value(integration.content.get("credential_ref"))),
            },
        )
    try:
        response, http_status = _call(integration, operation_row, payload)
    except TimeoutError:
        return IntegrationCallResult(False, integration.resource_key, operation, "failed", {}, "integration_timeout")
    except urllib.error.HTTPError as exc:
        return IntegrationCallResult(False, integration.resource_key, operation, "failed", {}, f"integration_http_{exc.code}", exc.code)
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        return IntegrationCallResult(False, integration.resource_key, operation, "failed", {}, f"integration_transport_{type(exc).__name__}")
    projected = _project_result(response, operation_row.get("result_allowlist") or [])
    return IntegrationCallResult(True, integration.resource_key, operation, "executed", projected, None, http_status)


def _resolve_integration(
    db: Session,
    *,
    integration_key: str,
    market_id: int | None,
    channel: str | None,
    language: str | None,
) -> ResolvedAgentConfig:
    key = str(integration_key or "").strip().lower()
    rows = resolve_published_agent_configs(
        db,
        config_type=INTEGRATION,
        market_id=market_id,
        channel=channel,
        language=language,
    )
    row = next(
        (
            item
            for item in rows
            if item.resource_key.lower() == key
            or str(item.content.get("name") or "").lower() == key
        ),
        None,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="integration_not_found")
    return row


def _operation(integration: ResolvedAgentConfig, operation: str) -> dict[str, Any]:
    key = str(operation or "").strip().lower()
    row = next(
        (
            item
            for item in integration.content.get("operations") or []
            if str(item.get("key") or "").strip().lower() == key
        ),
        None,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="integration_operation_not_found")
    return row


def _call(
    integration: ResolvedAgentConfig,
    operation: dict[str, Any],
    arguments: dict[str, Any],
) -> tuple[Any, int]:
    content = integration.content
    base_url = require_http_endpoint(str(content.get("base_url") or ""), label="integration base URL").rstrip("/")
    path = str(operation.get("path") or "")
    endpoint = require_http_endpoint(f"{base_url}{path}", label="integration endpoint")
    _enforce_host(endpoint, content.get("host_allowlist") or [])
    method = str(operation.get("method") or "GET").upper()
    headers = {"Accept": "application/json", "User-Agent": "Nexus-Agent-Integration/1.0"}
    credential = _credential_value(content.get("credential_ref"))
    if content.get("credential_ref") and not credential:
        raise ValueError("integration_credential_missing")
    if credential:
        headers["Authorization"] = f"Bearer {credential}"
    data = None
    if content.get("kind") == "mcp_http":
        method = "POST"
        data = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "nexus-agent",
                "method": "tools/call",
                "params": {"name": operation.get("key"), "arguments": arguments},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif method == "GET":
        query = urllib.parse.urlencode(_flat_query(arguments), doseq=True)
        endpoint = f"{endpoint}?{query}" if query else endpoint
    else:
        data = json.dumps(arguments, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(endpoint, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(_NoRedirect())
    timeout = float(content.get("timeout_seconds") or 12)
    with opener.open(request, timeout=timeout) as response:  # nosec B310
        limit = int(content.get("max_response_bytes") or 128000)
        raw = response.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("integration_response_too_large")
        status = int(getattr(response, "status", 200) or 200)
    decoded = json.loads(raw.decode("utf-8", errors="strict")) if raw else {}
    if content.get("kind") == "mcp_http" and isinstance(decoded, dict):
        if decoded.get("error"):
            raise ValueError("mcp_operation_failed")
        decoded = decoded.get("result", decoded)
    return decoded, status


def _credential_value(reference: Any) -> str | None:
    ref = str(reference or "").strip().lower()
    if not ref:
        return None
    if not _CREDENTIAL_RE.fullmatch(ref):
        return None
    suffix = re.sub(r"[^A-Z0-9]+", "_", ref.upper()).strip("_")
    file_path = str(os.getenv(f"NEXUS_CREDENTIAL_{suffix}_FILE") or "").strip()
    inline = str(os.getenv(f"NEXUS_CREDENTIAL_{suffix}") or "").strip()
    if file_path:
        try:
            with open(file_path, encoding="utf-8") as credential_file:
                return credential_file.read().strip() or None
        except OSError:
            return None
    app_env = str(os.getenv("APP_ENV") or "development").strip().lower()
    if app_env == "production":
        return None
    return inline or None


def _enforce_host(endpoint: str, allowlist: list[str]) -> None:
    parsed = urllib.parse.urlparse(endpoint)
    hostname = (parsed.hostname or "").lower()
    allowed = {str(item or "").strip().lower() for item in allowlist if str(item or "").strip()}
    if not hostname or hostname not in allowed:
        raise ValueError("integration_host_not_allowlisted")
    try:
        socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ValueError("integration_host_unresolvable") from exc


def _flat_query(arguments: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in list(arguments.items())[:100]:
        if value is None or isinstance(value, (str, int, float, bool)):
            output[str(key)[:120]] = value
        elif isinstance(value, list) and all(isinstance(item, (str, int, float, bool)) for item in value[:50]):
            output[str(key)[:120]] = value[:50]
        else:
            raise ValueError("integration_get_arguments_must_be_scalar")
    return output


def _project_result(value: Any, allowlist: list[str]) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {"value": value}
    if not allowlist:
        return _sanitize(source)
    output: dict[str, Any] = {}
    for raw_path in allowlist[:100]:
        path = [part for part in str(raw_path or "").split(".") if part]
        current: Any = source
        for part in path:
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current[part]
        if current is not None:
            output[str(raw_path)[:120]] = _sanitize(current)
    return output


def _sanitize(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:2000]
    if isinstance(value, list):
        return [_sanitize(item, depth=depth + 1) for item in value[:30]]
    if isinstance(value, dict):
        return {
            str(key)[:120]: _sanitize(item, depth=depth + 1)
            for key, item in list(value.items())[:100]
            if str(key).strip().lower() not in _SECRET_KEYS
        }
    return str(value)[:500]
