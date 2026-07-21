from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from jsonschema import Draft202012Validator
from sqlalchemy.orm import Session

from .agent_control_config import INTEGRATION, ResolvedAgentConfig
from .runtime_endpoint_policy import require_http_endpoint

_MCP_PROTOCOL_VERSION = "2025-11-25"
_MCP_COMPATIBLE_VERSIONS = frozenset({"2025-11-25", "2025-06-18"})
_MCP_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_CREDENTIAL_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,159}$")
_SECRET_KEYS = {
    "password",
    "secret",
    "token",
    "authorization",
    "api_key",
    "private_key",
    "cookie",
}
_SESSION_TTL_SECONDS = 600
_SESSION_LOCK = threading.RLock()
_MCP_SESSIONS: dict[str, tuple[str | None, str, float]] = {}


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


@dataclass(frozen=True)
class MCPDoctorReport:
    integration_key: str
    healthy: bool
    protocol_version: str | None
    server_info: dict[str, Any]
    capabilities: dict[str, Any]
    configured_tool_count: int
    discovered_tool_count: int
    schema_digest: str | None
    checks: tuple[dict[str, Any], ...]
    missing_tools: tuple[str, ...]
    schema_mismatches: tuple[str, ...]
    unmanaged_tools: tuple[str, ...]
    elapsed_ms: int

    def safe_summary(self) -> dict[str, Any]:
        return {
            "integration_key": self.integration_key,
            "healthy": self.healthy,
            "protocol_version": self.protocol_version,
            "server_info": dict(self.server_info),
            "capabilities": dict(self.capabilities),
            "configured_tool_count": self.configured_tool_count,
            "discovered_tool_count": self.discovered_tool_count,
            "schema_digest": self.schema_digest,
            "checks": list(self.checks),
            "missing_tools": list(self.missing_tools),
            "schema_mismatches": list(self.schema_mismatches),
            "unmanaged_tools": list(self.unmanaged_tools),
            "elapsed_ms": self.elapsed_ms,
        }


def list_integration_catalog(
    db: Session,
    *,
    market_id: int | None = None,
    channel: str | None = None,
    language: str | None = None,
    release_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    del db, market_id, channel, language
    rows = _released_integrations(release_snapshot)
    return [
        {
            **row.safe_summary(),
            "kind": row.content.get("kind"),
            "credential_configured": bool(
                _credential_value(row.content.get("credential_ref"))
            ),
            "operations": [
                {
                    "key": item.get("key"),
                    "description": item.get("description"),
                    "mode": item.get("mode"),
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
    release_snapshot: dict[str, Any] | None = None,
) -> IntegrationCallResult:
    del db, market_id, channel, language
    integration = _resolve_integration(
        integration_key=integration_key,
        release_snapshot=release_snapshot,
    )
    operation_row = _operation(integration, operation)
    mode = str(operation_row.get("mode") or "").strip().lower()
    is_write = mode == "write"
    if mode not in {"read", "write"} or is_write != bool(expected_write):
        return IntegrationCallResult(
            False,
            integration.resource_key,
            operation,
            "blocked",
            {},
            "integration_operation_classification_mismatch",
        )
    if operation_row.get("enabled") is False:
        return IntegrationCallResult(
            False,
            integration.resource_key,
            operation,
            "blocked",
            {},
            "integration_operation_disabled",
        )
    if is_write and not operation_row.get("requires_confirmation"):
        return IntegrationCallResult(
            False,
            integration.resource_key,
            operation,
            "blocked",
            {},
            "integration_write_confirmation_contract_missing",
        )
    result_allowlist = operation_row.get("result_allowlist")
    if not isinstance(result_allowlist, list) or not any(
        str(item or "").strip() for item in result_allowlist
    ):
        return IntegrationCallResult(
            False,
            integration.resource_key,
            operation,
            "blocked",
            {},
            "integration_result_projection_required",
        )
    payload = dict(arguments or {})
    validator = Draft202012Validator(
        operation_row.get("input_schema")
        or {"type": "object", "additionalProperties": False}
    )
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
                "mode": mode,
                "method": operation_row.get("method"),
                "endpoint_path": operation_row.get("path"),
                "credential_configured": bool(
                    _credential_value(integration.content.get("credential_ref"))
                ),
                "protocol_version": (
                    _MCP_PROTOCOL_VERSION
                    if integration.content.get("kind") == "mcp_http"
                    else None
                ),
            },
        )
    try:
        if integration.content.get("kind") == "mcp_http":
            response, http_status = _call_mcp_tool(
                integration,
                operation_row,
                payload,
            )
        else:
            response, http_status = _call_http_operation(
                integration,
                operation_row,
                payload,
            )
    except TimeoutError:
        return IntegrationCallResult(
            False,
            integration.resource_key,
            operation,
            "failed",
            {},
            "integration_timeout",
        )
    except urllib.error.HTTPError as exc:
        return IntegrationCallResult(
            False,
            integration.resource_key,
            operation,
            "failed",
            {},
            f"integration_http_{exc.code}",
            exc.code,
        )
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        return IntegrationCallResult(
            False,
            integration.resource_key,
            operation,
            "failed",
            {},
            f"integration_transport_{type(exc).__name__}",
        )
    projected = _project_result(response, result_allowlist)
    return IntegrationCallResult(
        True,
        integration.resource_key,
        operation,
        "executed",
        projected,
        None,
        http_status,
    )


def doctor_mcp_integration(
    db: Session,
    *,
    integration_key: str,
    release_snapshot: dict[str, Any],
) -> MCPDoctorReport:
    del db
    started = time.monotonic()
    integration = _resolve_integration(
        integration_key=integration_key,
        release_snapshot=release_snapshot,
    )
    if integration.content.get("kind") != "mcp_http":
        raise HTTPException(status_code=400, detail="integration_is_not_mcp_http")

    checks: list[dict[str, Any]] = []
    protocol_version: str | None = None
    server_info: dict[str, Any] = {}
    capabilities: dict[str, Any] = {}
    discovered: list[dict[str, Any]] = []
    try:
        session_id, protocol_version, initialize_result = _initialize_mcp_session(
            integration,
            force=True,
        )
        server_info = _safe_identity(initialize_result.get("serverInfo"))
        capabilities = _safe_capabilities(initialize_result.get("capabilities"))
        checks.append(_check("initialize", True, protocol_version))
        discovered = _list_mcp_tools(integration, session_id, protocol_version)
        checks.append(_check("tools_list", True, f"{len(discovered)} tools"))
    except Exception as exc:
        checks.append(_check("mcp_connection", False, type(exc).__name__))
        return MCPDoctorReport(
            integration_key=integration.resource_key,
            healthy=False,
            protocol_version=protocol_version,
            server_info=server_info,
            capabilities=capabilities,
            configured_tool_count=len(integration.content.get("operations") or []),
            discovered_tool_count=0,
            schema_digest=None,
            checks=tuple(checks),
            missing_tools=(),
            schema_mismatches=(),
            unmanaged_tools=(),
            elapsed_ms=_elapsed_ms(started),
        )

    configured = {
        str(item.get("key") or ""): item
        for item in integration.content.get("operations") or []
        if item.get("enabled") is not False
    }
    discovered_by_name = {str(item.get("name") or ""): item for item in discovered}
    missing = sorted(set(configured) - set(discovered_by_name))
    unmanaged = sorted(set(discovered_by_name) - set(configured))
    mismatches: list[str] = []
    for name in sorted(set(configured) & set(discovered_by_name)):
        expected_schema = configured[name].get("input_schema") or {
            "type": "object",
            "additionalProperties": False,
        }
        observed_schema = discovered_by_name[name].get("inputSchema") or {
            "type": "object"
        }
        if _digest(expected_schema) != _digest(observed_schema):
            mismatches.append(name)
    checks.append(
        _check(
            "configured_tools_present",
            not missing,
            "ok" if not missing else ",".join(missing[:10]),
        )
    )
    checks.append(
        _check(
            "input_schema_match",
            not mismatches,
            "ok" if not mismatches else ",".join(mismatches[:10]),
        )
    )
    checks.append(
        _check(
            "unmanaged_tools_not_exposed",
            True,
            f"{len(unmanaged)} ignored",
        )
    )
    schema_digest = _digest(
        [
            {
                "name": item.get("name"),
                "description": item.get("description"),
                "inputSchema": item.get("inputSchema"),
            }
            for item in discovered
        ]
    )
    healthy = not missing and not mismatches and all(
        bool(item.get("passed")) for item in checks
    )
    return MCPDoctorReport(
        integration_key=integration.resource_key,
        healthy=healthy,
        protocol_version=protocol_version,
        server_info=server_info,
        capabilities=capabilities,
        configured_tool_count=len(configured),
        discovered_tool_count=len(discovered),
        schema_digest=schema_digest,
        checks=tuple(checks),
        missing_tools=tuple(missing),
        schema_mismatches=tuple(mismatches),
        unmanaged_tools=tuple(unmanaged),
        elapsed_ms=_elapsed_ms(started),
    )


def _resolve_integration(
    *,
    integration_key: str,
    release_snapshot: dict[str, Any] | None,
) -> ResolvedAgentConfig:
    key = str(integration_key or "").strip().lower()
    rows = _released_integrations(release_snapshot)
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
        raise HTTPException(
            status_code=404,
            detail="integration_not_found_in_agent_release",
        )
    return row


def _released_integrations(
    release_snapshot: dict[str, Any] | None,
) -> list[ResolvedAgentConfig]:
    if (
        not isinstance(release_snapshot, dict)
        or release_snapshot.get("source") != "deployment"
    ):
        raise RuntimeError("agent_release_snapshot_required_for_integrations")
    resolved = release_snapshot.get("resolved")
    if not isinstance(resolved, dict):
        raise RuntimeError("agent_release_resolved_resources_missing")
    resources = resolved.get("resources")
    if not isinstance(resources, list):
        raise RuntimeError("agent_release_resources_invalid")
    output: list[ResolvedAgentConfig] = []
    for item in resources:
        if not isinstance(item, dict) or item.get("config_type") != INTEGRATION:
            continue
        content = item.get("content")
        if not isinstance(content, dict):
            raise RuntimeError("agent_release_integration_content_invalid")
        output.append(
            ResolvedAgentConfig(
                resource_id=int(item.get("id") or 0),
                resource_key=str(item.get("resource_key") or ""),
                config_type=INTEGRATION,
                content=content,
                version=int(item.get("version") or 0),
                scope_rank=100,
            )
        )
    return output


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


def _call_http_operation(
    integration: ResolvedAgentConfig,
    operation: dict[str, Any],
    arguments: dict[str, Any],
) -> tuple[Any, int]:
    content = integration.content
    base_url = _validated_base_url(content)
    path = str(operation.get("path") or "")
    endpoint = require_http_endpoint(
        f"{base_url}{path}",
        label="integration endpoint",
    )
    _enforce_host(endpoint, content.get("host_allowlist") or [])
    method = str(operation.get("method") or "GET").upper()
    headers = _headers(content)
    data = None
    if method == "GET":
        query = urllib.parse.urlencode(_flat_query(arguments), doseq=True)
        endpoint = f"{endpoint}?{query}" if query else endpoint
    else:
        data = _json_bytes(arguments)
        headers["Content-Type"] = "application/json"
    decoded, status, _response_headers = _request_json(
        endpoint,
        method=method,
        headers=headers,
        data=data,
        timeout=float(content.get("timeout_seconds") or 12),
        max_response_bytes=int(content.get("max_response_bytes") or 128000),
        allow_empty=False,
    )
    return decoded, status


def _call_mcp_tool(
    integration: ResolvedAgentConfig,
    operation: dict[str, Any],
    arguments: dict[str, Any],
) -> tuple[Any, int]:
    session_id, protocol_version, _ = _initialize_mcp_session(integration)
    try:
        result, status, _headers = _mcp_rpc(
            integration,
            method="tools/call",
            params={"name": operation.get("key"), "arguments": arguments},
            request_id="nexus-agent-tool",
            session_id=session_id,
            protocol_version=protocol_version,
        )
    except urllib.error.HTTPError as exc:
        if exc.code not in {400, 404, 409, 410}:
            raise
        _clear_mcp_session(integration)
        session_id, protocol_version, _ = _initialize_mcp_session(
            integration,
            force=True,
        )
        result, status, _headers = _mcp_rpc(
            integration,
            method="tools/call",
            params={"name": operation.get("key"), "arguments": arguments},
            request_id="nexus-agent-tool-retry",
            session_id=session_id,
            protocol_version=protocol_version,
        )
    if isinstance(result, dict) and result.get("isError") is True:
        raise ValueError("mcp_operation_failed")
    if isinstance(result, dict) and isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"], status
    return result, status


def _initialize_mcp_session(
    integration: ResolvedAgentConfig,
    *,
    force: bool = False,
) -> tuple[str | None, str, dict[str, Any]]:
    key = _mcp_session_key(integration)
    if not force:
        with _SESSION_LOCK:
            cached = _MCP_SESSIONS.get(key)
            if cached and cached[2] > time.monotonic():
                return cached[0], cached[1], {}
    result, _status, headers = _mcp_rpc(
        integration,
        method="initialize",
        params={
            "protocolVersion": _MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "nexus-agent", "version": "1.0.0"},
        },
        request_id="nexus-mcp-initialize",
        session_id=None,
        protocol_version=_MCP_PROTOCOL_VERSION,
    )
    if not isinstance(result, dict):
        raise ValueError("mcp_initialize_result_invalid")
    negotiated = str(result.get("protocolVersion") or "").strip()
    if negotiated not in _MCP_COMPATIBLE_VERSIONS:
        raise ValueError("mcp_protocol_version_unsupported")
    capabilities = result.get("capabilities")
    if not isinstance(capabilities, dict) or not isinstance(
        capabilities.get("tools"), dict
    ):
        raise ValueError("mcp_tools_capability_missing")
    session_id = _header(headers, "Mcp-Session-Id")
    _mcp_rpc(
        integration,
        method="notifications/initialized",
        params={},
        request_id=None,
        session_id=session_id,
        protocol_version=negotiated,
        notification=True,
    )
    with _SESSION_LOCK:
        _MCP_SESSIONS[key] = (
            session_id,
            negotiated,
            time.monotonic() + _SESSION_TTL_SECONDS,
        )
    return session_id, negotiated, result


def _list_mcp_tools(
    integration: ResolvedAgentConfig,
    session_id: str | None,
    protocol_version: str,
) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    for page in range(10):
        params = {"cursor": cursor} if cursor else {}
        result, _status, _headers = _mcp_rpc(
            integration,
            method="tools/list",
            params=params,
            request_id=f"nexus-mcp-tools-{page}",
            session_id=session_id,
            protocol_version=protocol_version,
        )
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            raise ValueError("mcp_tools_list_invalid")
        for raw in result["tools"]:
            tool = _normalize_mcp_tool(raw)
            if any(item["name"] == tool["name"] for item in tools):
                raise ValueError("mcp_duplicate_tool_name")
            tools.append(tool)
            if len(tools) > 200:
                raise ValueError("mcp_tool_catalog_too_large")
        next_cursor = str(result.get("nextCursor") or "").strip() or None
        if not next_cursor:
            break
        if next_cursor in seen_cursors:
            raise ValueError("mcp_cursor_cycle")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    return sorted(tools, key=lambda item: item["name"])


def _normalize_mcp_tool(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("mcp_tool_invalid")
    name = str(value.get("name") or "").strip()
    if not _MCP_TOOL_NAME_RE.fullmatch(name):
        raise ValueError("mcp_tool_name_invalid")
    schema = value.get("inputSchema") or {"type": "object"}
    if not isinstance(schema, dict):
        raise ValueError("mcp_tool_schema_invalid")
    Draft202012Validator.check_schema(schema)
    return {
        "name": name,
        "title": str(value.get("title") or "")[:200] or None,
        "description": str(value.get("description") or "")[:2000],
        "inputSchema": schema,
    }


def _mcp_rpc(
    integration: ResolvedAgentConfig,
    *,
    method: str,
    params: dict[str, Any],
    request_id: str | None,
    session_id: str | None,
    protocol_version: str,
    notification: bool = False,
) -> tuple[Any, int, dict[str, str]]:
    content = integration.content
    endpoint = _validated_base_url(content)
    headers = _headers(content)
    headers.update(
        {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": protocol_version,
        }
    )
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if not notification:
        body["id"] = request_id or "nexus-mcp"
    if params or method in {"initialize", "tools/list", "tools/call"}:
        body["params"] = params
    decoded, status, response_headers = _request_json(
        endpoint,
        method="POST",
        headers=headers,
        data=_json_bytes(body),
        timeout=float(content.get("timeout_seconds") or 12),
        max_response_bytes=int(content.get("max_response_bytes") or 128000),
        allow_empty=notification,
    )
    if notification:
        return {}, status, response_headers
    if not isinstance(decoded, dict):
        raise ValueError("mcp_response_not_object")
    if decoded.get("error"):
        raise ValueError("mcp_protocol_error")
    if decoded.get("id") not in {request_id, str(request_id)}:
        raise ValueError("mcp_response_id_mismatch")
    if "result" not in decoded:
        raise ValueError("mcp_result_missing")
    return decoded["result"], status, response_headers


def _request_json(
    endpoint: str,
    *,
    method: str,
    headers: dict[str, str],
    data: bytes | None,
    timeout: float,
    max_response_bytes: int,
    allow_empty: bool,
) -> tuple[Any, int, dict[str, str]]:
    request = urllib.request.Request(
        endpoint,
        data=data,
        headers=headers,
        method=method,
    )
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(request, timeout=timeout) as response:  # nosec B310
        limit = max(1024, min(int(max_response_bytes), 1_000_000))
        raw = response.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("integration_response_too_large")
        status = int(getattr(response, "status", 200) or 200)
        response_headers = {str(k): str(v) for k, v in response.headers.items()}
        content_type = str(response.headers.get("Content-Type") or "").lower()
    if not raw:
        if allow_empty:
            return {}, status, response_headers
        return {}, status, response_headers
    decoded = _decode_response(raw, content_type)
    return decoded, status, response_headers


def _decode_response(raw: bytes, content_type: str) -> Any:
    text = raw.decode("utf-8", errors="strict")
    if "text/event-stream" in content_type or text.lstrip().startswith("data:"):
        payloads = [
            line[5:].strip()
            for line in text.splitlines()
            if line.startswith("data:") and line[5:].strip() not in {"", "[DONE]"}
        ]
        if not payloads:
            raise ValueError("mcp_sse_payload_missing")
        return json.loads(payloads[-1])
    return json.loads(text)


def _validated_base_url(content: dict[str, Any]) -> str:
    endpoint = require_http_endpoint(
        str(content.get("base_url") or ""),
        label="integration base URL",
    ).rstrip("/")
    _enforce_host(endpoint, content.get("host_allowlist") or [])
    return endpoint


def _headers(content: dict[str, Any]) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "Nexus-Agent-Integration/1.0",
    }
    credential = _credential_value(content.get("credential_ref"))
    if content.get("credential_ref") and not credential:
        raise ValueError("integration_credential_missing")
    if credential:
        headers["Authorization"] = f"Bearer {credential}"
    return headers


def _credential_value(reference: Any) -> str | None:
    ref = str(reference or "").strip().lower()
    if not ref or not _CREDENTIAL_RE.fullmatch(ref):
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
    if str(os.getenv("APP_ENV") or "development").strip().lower() == "production":
        return None
    return inline or None


def _enforce_host(endpoint: str, allowlist: list[str]) -> None:
    parsed = urllib.parse.urlparse(endpoint)
    hostname = (parsed.hostname or "").lower()
    allowed = {
        str(item or "").strip().lower()
        for item in allowlist
        if str(item or "").strip()
    }
    if not hostname or hostname not in allowed:
        raise ValueError("integration_host_not_allowlisted")
    try:
        records = socket.getaddrinfo(
            hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ValueError("integration_host_unresolvable") from exc
    if not records:
        raise ValueError("integration_host_unresolvable")
    for record in records:
        address = ipaddress.ip_address(record[4][0])
        if not address.is_global:
            raise ValueError("integration_private_address_forbidden")


def _flat_query(arguments: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in list(arguments.items())[:100]:
        if value is None or isinstance(value, (str, int, float, bool)):
            output[str(key)[:120]] = value
        elif isinstance(value, list) and all(
            isinstance(item, (str, int, float, bool)) for item in value[:50]
        ):
            output[str(key)[:120]] = value[:50]
        else:
            raise ValueError("integration_get_arguments_must_be_scalar")
    return output


def _project_result(value: Any, allowlist: list[str]) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {"value": value}
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
        return value[:4000]
    if isinstance(value, list):
        return [_sanitize(item, depth=depth + 1) for item in value[:100]]
    if isinstance(value, dict):
        return {
            str(key)[:100]: _sanitize(item, depth=depth + 1)
            for key, item in list(value.items())[:100]
            if str(key).strip().lower() not in _SECRET_KEYS
        }
    return str(value)[:500]


def _mcp_session_key(integration: ResolvedAgentConfig) -> str:
    return hashlib.sha256(
        "|".join(
            (
                integration.resource_key,
                str(integration.version),
                str(integration.content.get("base_url") or ""),
                str(integration.content.get("credential_ref") or ""),
            )
        ).encode("utf-8")
    ).hexdigest()


def _clear_mcp_session(integration: ResolvedAgentConfig) -> None:
    with _SESSION_LOCK:
        _MCP_SESSIONS.pop(_mcp_session_key(integration), None)


def _safe_identity(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "name": str(value.get("name") or "")[:160],
        "title": str(value.get("title") or "")[:200] or None,
        "version": str(value.get("version") or "")[:80],
    }


def _safe_capabilities(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key)[:80]: _sanitize(item)
        for key, item in list(value.items())[:40]
        if isinstance(item, dict)
    }


def _header(headers: dict[str, str], name: str) -> str | None:
    expected = name.lower()
    for key, value in headers.items():
        if key.lower() == expected:
            return str(value).strip() or None
    return None


def _check(label: str, passed: bool, detail: str | None) -> dict[str, Any]:
    return {
        "label": label[:80],
        "passed": bool(passed),
        "detail": str(detail or "")[:240] or None,
    }


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))
