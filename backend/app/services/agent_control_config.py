from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import AIConfigResource
from .webchat_ai_decision_runtime.tool_registry import get_tool_contract

PLAYBOOK = "playbook"
INTEGRATION = "integration"
MODEL_PROFILE = "model_profile"
RUNTIME_POLICY = "runtime_policy"
MEMORY_POLICY = "memory_policy"
CANONICAL_AGENT_CONFIG_TYPES = {
    PLAYBOOK,
    INTEGRATION,
    MODEL_PROFILE,
    RUNTIME_POLICY,
    MEMORY_POLICY,
}
SINGLETON_TYPES = {MODEL_PROFILE, RUNTIME_POLICY, MEMORY_POLICY}
SAFE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,159}$")
SECRET_FIELD_FRAGMENTS = (
    "password",
    "secret",
    "token",
    "authorization",
    "api_key",
    "private_key",
    "cookie",
)


@dataclass(frozen=True)
class ResolvedAgentConfig:
    resource_id: int
    resource_key: str
    config_type: str
    content: dict[str, Any]
    version: int
    scope_rank: int

    def safe_summary(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "resource_key": self.resource_key,
            "config_type": self.config_type,
            "published_version": self.version,
            "scope_rank": self.scope_rank,
        }


def validate_agent_config_content(config_type: str, content: Any) -> dict[str, Any]:
    if config_type not in CANONICAL_AGENT_CONFIG_TYPES:
        raise HTTPException(status_code=400, detail="unsupported_agent_config_type")
    if not isinstance(content, dict):
        raise HTTPException(status_code=400, detail="agent_config_content_must_be_object")
    _reject_secret_fields(content)
    if config_type == PLAYBOOK:
        return _validate_playbook(content)
    if config_type == INTEGRATION:
        return _validate_integration(content)
    if config_type == MODEL_PROFILE:
        return _validate_model_profile(content)
    if config_type == RUNTIME_POLICY:
        return _validate_runtime_policy(content)
    return _validate_memory_policy(content)


def resolve_published_agent_configs(
    db: Session,
    *,
    config_type: str,
    market_id: int | None = None,
    channel: str | None = None,
    language: str | None = None,
) -> list[ResolvedAgentConfig]:
    if config_type not in CANONICAL_AGENT_CONFIG_TYPES:
        return []
    query = db.query(AIConfigResource).filter(
        AIConfigResource.config_type == config_type,
        AIConfigResource.is_active.is_(True),
        AIConfigResource.published_version > 0,
    )
    if market_id is not None:
        query = query.filter(
            or_(AIConfigResource.market_id.is_(None), AIConfigResource.market_id == market_id)
        )
    rows = query.order_by(
        AIConfigResource.market_id.desc().nullslast(),
        AIConfigResource.published_version.desc(),
        AIConfigResource.resource_key.asc(),
    ).all()
    resolved: list[ResolvedAgentConfig] = []
    for row in rows:
        content = row.published_content_json if isinstance(row.published_content_json, dict) else {}
        if not content or content.get("enabled") is False:
            continue
        rank = _scope_rank(
            row,
            content,
            market_id=market_id,
            channel=channel,
            language=language,
        )
        if rank < 0:
            continue
        try:
            validated = validate_agent_config_content(config_type, content)
        except HTTPException:
            continue
        resolved.append(
            ResolvedAgentConfig(
                resource_id=row.id,
                resource_key=row.resource_key,
                config_type=row.config_type,
                content=validated,
                version=row.published_version,
                scope_rank=rank,
            )
        )
    resolved.sort(
        key=lambda item: (
            -item.scope_rank,
            int(item.content.get("priority") or 100),
            item.resource_key,
        )
    )
    return resolved


def resolve_singleton_agent_config(
    db: Session,
    *,
    config_type: str,
    market_id: int | None = None,
    channel: str | None = None,
    language: str | None = None,
) -> ResolvedAgentConfig | None:
    rows = resolve_published_agent_configs(
        db,
        config_type=config_type,
        market_id=market_id,
        channel=channel,
        language=language,
    )
    return rows[0] if rows else None


def safe_resource_payload(row: AIConfigResource) -> dict[str, Any]:
    return {
        "id": row.id,
        "resource_key": row.resource_key,
        "config_type": row.config_type,
        "name": row.name,
        "description": row.description,
        "scope_type": row.scope_type,
        "scope_value": row.scope_value,
        "market_id": row.market_id,
        "is_active": row.is_active,
        "draft_summary": row.draft_summary,
        "draft_content_json": _safe_projection(row.draft_content_json),
        "published_summary": row.published_summary,
        "published_content_json": _safe_projection(row.published_content_json),
        "published_version": row.published_version,
        "published_at": row.published_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _scope_rank(
    row: AIConfigResource,
    content: dict[str, Any],
    *,
    market_id: int | None,
    channel: str | None,
    language: str | None,
) -> int:
    rank = 0
    if row.market_id is not None:
        if market_id != row.market_id:
            return -1
        rank += 16
    scope_type = str(row.scope_type or "global").strip().lower()
    scope_value = str(row.scope_value or "").strip().lower()
    normalized_channel = str(channel or "").strip().lower()
    normalized_language = str(language or "").strip().lower()
    if scope_type == "channel":
        if not scope_value or scope_value != normalized_channel:
            return -1
        rank += 8
    elif scope_type == "market":
        if not scope_value or scope_value not in {str(market_id or ""), "global"}:
            return -1
        rank += 8
    elif scope_type not in {"global", "team", "case_type"}:
        return -1
    channels = _string_list(content.get("channels"), max_items=30, max_chars=40)
    if channels:
        normalized = {item.lower() for item in channels}
        if normalized_channel not in normalized and "all" not in normalized:
            return -1
        rank += 4
    languages = _string_list(content.get("languages"), max_items=30, max_chars=24)
    if languages:
        normalized = {item.lower() for item in languages}
        if normalized_language not in normalized and "all" not in normalized:
            return -1
        rank += 2
    return rank


def _validate_playbook(content: dict[str, Any]) -> dict[str, Any]:
    name = _safe_key(content.get("name"), "playbook_name")
    description = _required_text(content.get("description"), "playbook_description", 1200)
    tools = _string_list(content.get("tools"), max_items=40, max_chars=160)
    unknown = [name for name in tools if get_tool_contract(name) is None]
    if unknown:
        raise HTTPException(status_code=400, detail={"error_code": "unknown_playbook_tools", "tools": unknown})
    instructions = _string_list(content.get("instructions"), max_items=50, max_chars=1600)
    if not instructions:
        raise HTTPException(status_code=400, detail="playbook_instructions_required")
    return {
        "schema_version": "nexus.agent_playbook.v1",
        "name": name,
        "display_name": _optional_text(content.get("display_name"), 160) or name,
        "description": description,
        "tools": tools,
        "instructions": instructions,
        "priority": _bounded_int(content.get("priority"), 100, 0, 10000),
        "channels": _string_list(content.get("channels"), max_items=30, max_chars=40),
        "languages": _string_list(content.get("languages"), max_items=30, max_chars=24),
        "enabled": content.get("enabled") is not False,
    }


def _validate_integration(content: dict[str, Any]) -> dict[str, Any]:
    kind = str(content.get("kind") or "http").strip().lower()
    if kind not in {"http", "mcp_http"}:
        raise HTTPException(status_code=400, detail="integration_kind_not_allowed")
    base_url = _required_http_url(content.get("base_url"), "integration_base_url")
    credential_ref = _optional_key(content.get("credential_ref"), "credential_ref")
    host_allowlist = _string_list(content.get("host_allowlist"), max_items=20, max_chars=253)
    parsed = urlparse(base_url)
    if host_allowlist and (parsed.hostname or "").lower() not in {item.lower() for item in host_allowlist}:
        raise HTTPException(status_code=400, detail="integration_host_not_allowlisted")
    operations: list[dict[str, Any]] = []
    raw_operations = content.get("operations")
    if not isinstance(raw_operations, list) or not raw_operations:
        raise HTTPException(status_code=400, detail="integration_operations_required")
    for raw in raw_operations[:50]:
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail="integration_operation_must_be_object")
        method = str(raw.get("method") or "GET").strip().upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise HTTPException(status_code=400, detail="integration_method_not_allowed")
        path = str(raw.get("path") or "").strip()
        if not path.startswith("/") or ".." in path or "://" in path:
            raise HTTPException(status_code=400, detail="integration_path_invalid")
        schema = raw.get("input_schema") or {"type": "object", "additionalProperties": False}
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise HTTPException(status_code=400, detail="integration_input_schema_invalid")
        operations.append(
            {
                "key": _safe_key(raw.get("key"), "integration_operation_key"),
                "description": _required_text(raw.get("description"), "integration_operation_description", 600),
                "method": method,
                "path": path[:500],
                "input_schema": schema,
                "result_allowlist": _string_list(raw.get("result_allowlist"), max_items=100, max_chars=120),
                "risk_level": _enum(raw.get("risk_level"), {"low", "medium", "high"}, "medium"),
                "requires_confirmation": bool(raw.get("requires_confirmation")),
                "enabled": raw.get("enabled") is not False,
            }
        )
    return {
        "schema_version": "nexus.agent_integration.v1",
        "kind": kind,
        "base_url": base_url,
        "credential_ref": credential_ref,
        "host_allowlist": host_allowlist or [parsed.hostname or ""],
        "timeout_seconds": _bounded_int(content.get("timeout_seconds"), 12, 1, 30),
        "max_response_bytes": _bounded_int(content.get("max_response_bytes"), 128000, 1000, 1000000),
        "operations": operations,
        "enabled": content.get("enabled") is not False,
    }


def _validate_model_profile(content: dict[str, Any]) -> dict[str, Any]:
    provider = _enum(content.get("provider"), {"private_ai_runtime"}, "private_ai_runtime")
    endpoint = content.get("endpoint_url")
    endpoint_url = _required_http_url(endpoint, "model_endpoint_url") if endpoint else None
    return {
        "schema_version": "nexus.agent_model_profile.v1",
        "provider": provider,
        "endpoint_url": endpoint_url,
        "credential_ref": _optional_key(content.get("credential_ref"), "credential_ref"),
        "request_path": _safe_path(content.get("request_path") or "/api/chat"),
        "request_shape": _enum(content.get("request_shape"), {"system_input", "messages", "ollama_chat", "question"}, "ollama_chat"),
        "model": _required_text(content.get("model"), "model_name", 200),
        "temperature": _bounded_float(content.get("temperature"), 0.1, 0, 2),
        "top_p": _bounded_float(content.get("top_p"), 0.85, 0, 1),
        "max_prompt_chars": _bounded_int(content.get("max_prompt_chars"), 12000, 2000, 30000),
        "max_output_chars": _bounded_int(content.get("max_output_chars"), 4000, 500, 8000),
        "num_predict": _bounded_int(content.get("num_predict"), 512, 96, 2048),
        "num_ctx": _bounded_int(content.get("num_ctx"), 8192, 1024, 32768),
        "keep_alive": _optional_text(content.get("keep_alive"), 32) or "24h",
        "timeout_seconds": _bounded_int(content.get("timeout_seconds"), 12, 1, 60),
        "enabled": content.get("enabled") is not False,
    }


def _validate_runtime_policy(content: dict[str, Any]) -> dict[str, Any]:
    tools = _string_list(content.get("allowed_tools"), max_items=100, max_chars=160)
    unknown = [name for name in tools if get_tool_contract(name) is None]
    if unknown:
        raise HTTPException(status_code=400, detail={"error_code": "unknown_runtime_tools", "tools": unknown})
    return {
        "schema_version": "nexus.agent_runtime_policy.v1",
        "max_tool_rounds": _bounded_int(content.get("max_tool_rounds"), 3, 1, 6),
        "allow_high_risk_writes": bool(content.get("allow_high_risk_writes")),
        "allowed_tools": tools,
        "provider_timeout_ms": _bounded_int(content.get("provider_timeout_ms"), 15000, 1000, 30000),
        "enabled": content.get("enabled") is not False,
    }


def _validate_memory_policy(content: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "nexus.customer_memory_policy.v1",
        "injection_enabled": content.get("injection_enabled") is not False,
        "write_enabled": bool(content.get("write_enabled")),
        "require_explicit_consent": content.get("require_explicit_consent") is not False,
        "max_facts": _bounded_int(content.get("max_facts"), 12, 0, 50),
        "retention_days": _bounded_int(content.get("retention_days"), 180, 1, 3650),
        "allowed_keys": [_safe_key(item, "memory_key") for item in _string_list(content.get("allowed_keys"), max_items=100, max_chars=120)],
        "prohibited_categories": _string_list(content.get("prohibited_categories"), max_items=50, max_chars=80),
        "enabled": content.get("enabled") is not False,
    }


def _reject_secret_fields(value: Any, *, path: str = "") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if any(fragment in normalized for fragment in SECRET_FIELD_FRAGMENTS) and normalized != "credential_ref":
                raise HTTPException(status_code=400, detail={"error_code": "secret_value_not_allowed", "field": f"{path}.{key}".strip(".")})
            _reject_secret_fields(item, path=f"{path}.{key}".strip("."))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_fields(item, path=f"{path}[{index}]")


def _safe_projection(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): ("[credential-reference]" if str(key).lower() == "credential_ref" and item else _safe_projection(item))
            for key, item in value.items()
            if not any(fragment in str(key).lower() for fragment in SECRET_FIELD_FRAGMENTS if fragment != "credential_ref")
        }
    if isinstance(value, list):
        return [_safe_projection(item) for item in value[:100]]
    if isinstance(value, str):
        return value[:4000]
    return value


def _safe_key(value: Any, label: str) -> str:
    cleaned = str(value or "").strip().lower()
    if not SAFE_KEY_RE.fullmatch(cleaned):
        raise HTTPException(status_code=400, detail=f"{label}_invalid")
    return cleaned


def _optional_key(value: Any, label: str) -> str | None:
    if value in (None, ""):
        return None
    return _safe_key(value, label)


def _required_text(value: Any, label: str, max_chars: int) -> str:
    cleaned = " ".join(str(value or "").strip().split())
    if not cleaned:
        raise HTTPException(status_code=400, detail=f"{label}_required")
    return cleaned[:max_chars]


def _optional_text(value: Any, max_chars: int) -> str | None:
    cleaned = " ".join(str(value or "").strip().split())
    return cleaned[:max_chars] if cleaned else None


def _string_list(value: Any, *, max_items: int, max_chars: int) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise HTTPException(status_code=400, detail="list_value_required")
    output: list[str] = []
    for item in list(value)[:max_items]:
        cleaned = " ".join(str(item or "").strip().split())
        if cleaned and cleaned not in output:
            output.append(cleaned[:max_chars])
    return output


def _required_http_url(value: Any, label: str) -> str:
    cleaned = str(value or "").strip().rstrip("/")
    parsed = urlparse(cleaned)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail=f"{label}_invalid")
    if parsed.username or parsed.password or parsed.fragment:
        raise HTTPException(status_code=400, detail=f"{label}_credentials_or_fragment_forbidden")
    return cleaned


def _safe_path(value: Any) -> str:
    cleaned = str(value or "").strip()
    if not cleaned.startswith("/") or ".." in cleaned or "://" in cleaned:
        raise HTTPException(status_code=400, detail="request_path_invalid")
    return cleaned[:500]


def _enum(value: Any, allowed: Iterable[str], default: str) -> str:
    cleaned = str(value or default).strip().lower()
    if cleaned not in set(allowed):
        raise HTTPException(status_code=400, detail="enum_value_not_allowed")
    return cleaned


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value if value is not None else default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))
