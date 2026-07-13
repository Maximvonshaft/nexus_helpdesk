from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping

RESOLVED_SCOPE_SCHEMA = "nexus.resolved-scope.v1"
_FORBIDDEN_SCOPE_VALUES = frozenset({"*", "all", "any", "default", "global"})
_TOKEN_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,119}$")


class ScopeError(ValueError):
    """Base class for fail-closed operational-scope errors."""


class ScopeMissingError(ScopeError):
    pass


class ScopeConflictError(ScopeError):
    pass


class UnsafeLegacyScopeError(ScopeError):
    pass


class ProtectedOperation(str, Enum):
    RUNTIME = "runtime"
    KNOWLEDGE_READ = "knowledge_read"
    KNOWLEDGE_WRITE = "knowledge_write"
    TOOL_DECISION = "tool_decision"
    OUTBOX_WRITE = "outbox_write"
    AUTHORIZED_QUERY = "authorized_query"
    WORKER = "worker"
    AUDIT_WRITE = "audit_write"


@dataclass(frozen=True, slots=True)
class ScopeAuthority:
    tenant_id: int | None
    tenant_key: str | None
    market_id: int | None = None
    market_key: str | None = None
    country: str | None = None
    channel: str | None = None


@dataclass(frozen=True, slots=True)
class ScopeProjection:
    brand: str | None = None
    country: str | None = None
    channel: str | None = None
    locale: str | None = None
    audience: str | None = None
    visibility: str | None = None
    shareability: str | None = None


def _text(value: object | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _token(value: object | None, *, field_name: str, required: bool = False) -> str | None:
    normalized = _text(value)
    if normalized is None:
        if required:
            raise ScopeMissingError(f"missing {field_name}")
        return None
    normalized = re.sub(r"\s+", "_", normalized.lower())
    if normalized in _FORBIDDEN_SCOPE_VALUES:
        raise UnsafeLegacyScopeError(f"unsafe {field_name}: {normalized}")
    if len(normalized) > 120 or not _TOKEN_PATTERN.fullmatch(normalized):
        raise UnsafeLegacyScopeError(f"invalid {field_name}")
    return normalized


def _tenant_key(value: object | None) -> str:
    raw = _text(value)
    if raw is None:
        raise ScopeMissingError("missing tenant_key")
    normalized = raw.lower()
    if re.search(r"\s", normalized):
        raise UnsafeLegacyScopeError("invalid tenant_key")
    if normalized in _FORBIDDEN_SCOPE_VALUES:
        raise UnsafeLegacyScopeError(f"unsafe tenant_key: {normalized}")
    if len(normalized) > 80 or not _TOKEN_PATTERN.fullmatch(normalized):
        raise UnsafeLegacyScopeError("invalid tenant_key")
    return normalized


def _country(value: object | None, *, required: bool = False) -> str | None:
    normalized = _text(value)
    if normalized is None:
        if required:
            raise ScopeMissingError("missing country")
        return None
    lowered = normalized.lower()
    if lowered in _FORBIDDEN_SCOPE_VALUES:
        raise UnsafeLegacyScopeError(f"unsafe country: {lowered}")
    upper = normalized.upper()
    if not re.fullmatch(r"[A-Z]{2,3}", upper):
        raise UnsafeLegacyScopeError("invalid country")
    return upper


def _locale(value: object | None) -> str | None:
    normalized = _text(value)
    if normalized is None:
        return None
    if normalized.lower() in _FORBIDDEN_SCOPE_VALUES:
        raise UnsafeLegacyScopeError(f"unsafe locale: {normalized.lower()}")
    parts = normalized.replace("_", "-").split("-")
    if not parts or not re.fullmatch(r"[A-Za-z]{2,3}", parts[0]):
        raise UnsafeLegacyScopeError("invalid locale")
    result = [parts[0].lower()]
    for part in parts[1:]:
        if re.fullmatch(r"[A-Za-z]{2}", part):
            result.append(part.upper())
        elif re.fullmatch(r"[A-Za-z0-9]{3,8}", part):
            result.append(part.lower())
        else:
            raise UnsafeLegacyScopeError("invalid locale")
    return "-".join(result)


def _positive_id(value: object | None, *, field_name: str, required: bool = False) -> int | None:
    if value is None:
        if required:
            raise ScopeMissingError(f"missing {field_name}")
        return None
    if isinstance(value, bool):
        raise UnsafeLegacyScopeError(f"invalid {field_name}")
    try:
        resolved = int(value)
    except (TypeError, ValueError) as exc:
        raise UnsafeLegacyScopeError(f"invalid {field_name}") from exc
    if resolved <= 0:
        raise UnsafeLegacyScopeError(f"invalid {field_name}")
    return resolved


def _fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ResolvedScope:
    tenant_id: int
    tenant_key: str
    brand: str
    market_id: int | None
    market: str | None
    country: str | None
    channel: str
    locale: str | None
    audience: str | None
    visibility: str | None
    shareability: str | None
    operation: ProtectedOperation
    fallbacks: tuple[str, ...] = field(default_factory=tuple)
    schema: str = RESOLVED_SCOPE_SCHEMA
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if self.schema != RESOLVED_SCOPE_SCHEMA:
            raise UnsafeLegacyScopeError("invalid resolved scope schema")
        try:
            operation = ProtectedOperation(self.operation)
        except (TypeError, ValueError) as exc:
            raise UnsafeLegacyScopeError("invalid protected operation") from exc
        fallbacks = tuple(self.fallbacks)
        if any(item != "brand<-tenant_key" for item in fallbacks):
            raise UnsafeLegacyScopeError("invalid scope fallback")
        normalized = {
            "tenant_id": _positive_id(self.tenant_id, field_name="tenant_id", required=True),
            "tenant_key": _tenant_key(self.tenant_key),
            "brand": _token(self.brand, field_name="brand", required=True),
            "market_id": _positive_id(self.market_id, field_name="market_id"),
            "market": _token(self.market, field_name="market"),
            "country": _country(self.country),
            "channel": _token(self.channel, field_name="channel", required=True),
            "locale": _locale(self.locale),
            "audience": _token(self.audience, field_name="audience"),
            "visibility": _token(self.visibility, field_name="visibility"),
            "shareability": _token(self.shareability, field_name="shareability"),
            "operation": operation,
            "fallbacks": fallbacks,
        }
        if operation == ProtectedOperation.KNOWLEDGE_READ:
            if normalized["visibility"] != "customer":
                raise ScopeConflictError("visibility scope conflict")
            if normalized["shareability"] != "customer_visible":
                raise ScopeConflictError("shareability scope conflict")
            if normalized["audience"] != "customer":
                raise ScopeConflictError("audience scope conflict")
        for field_name, value in normalized.items():
            object.__setattr__(self, field_name, value)
        payload = self._payload(include_fingerprint=False)
        object.__setattr__(self, "fingerprint", _fingerprint(payload))

    def _payload(self, *, include_fingerprint: bool) -> dict[str, Any]:
        payload = {
            "schema": self.schema,
            "tenant_id": self.tenant_id,
            "tenant_key": self.tenant_key,
            "brand": self.brand,
            "market_id": self.market_id,
            "market": self.market,
            "country": self.country,
            "channel": self.channel,
            "locale": self.locale,
            "audience": self.audience,
            "visibility": self.visibility,
            "shareability": self.shareability,
            "operation": self.operation.value,
            "fallbacks": list(self.fallbacks),
        }
        if include_fingerprint:
            payload["fingerprint"] = self.fingerprint
        return payload

    def as_dict(self) -> dict[str, Any]:
        return self._payload(include_fingerprint=True)

    def assert_matches(self, candidate: Mapping[str, Any]) -> None:
        expected = self.as_dict()
        normalizers = {
            "tenant_id": lambda value: _positive_id(value, field_name="tenant_id", required=True),
            "tenant_key": _tenant_key,
            "brand": lambda value: _token(value, field_name="brand", required=True),
            "market_id": lambda value: _positive_id(value, field_name="market_id"),
            "market": lambda value: _token(value, field_name="market"),
            "country": lambda value: _country(value),
            "channel": lambda value: _token(value, field_name="channel", required=True),
            "locale": _locale,
            "audience": lambda value: _token(value, field_name="audience"),
            "visibility": lambda value: _token(value, field_name="visibility"),
            "shareability": lambda value: _token(value, field_name="shareability"),
        }
        for field_name, normalize in normalizers.items():
            if field_name not in candidate:
                raise ScopeMissingError(f"missing {field_name}")
            actual = normalize(candidate[field_name])
            if actual != expected[field_name]:
                raise ScopeConflictError(f"{field_name} scope conflict")


def resolve_scope(
    *,
    authority: ScopeAuthority,
    projection: ScopeProjection,
    operation: ProtectedOperation,
) -> ResolvedScope:
    try:
        resolved_operation = ProtectedOperation(operation)
    except (TypeError, ValueError) as exc:
        raise UnsafeLegacyScopeError("invalid protected operation") from exc
    tenant_id = _positive_id(authority.tenant_id, field_name="tenant_id", required=True)
    assert tenant_id is not None
    tenant_key = _tenant_key(authority.tenant_key)
    channel = _token(authority.channel, field_name="channel", required=True)
    assert channel is not None
    market_id = _positive_id(authority.market_id, field_name="market_id")
    market = _token(authority.market_key, field_name="market")
    country = _country(authority.country)

    projected_country = _country(projection.country)
    if country is not None and projected_country is not None and projected_country != country:
        raise ScopeConflictError("country scope conflict")
    resolved_country = country or projected_country

    projected_channel = _token(projection.channel, field_name="channel")
    if projected_channel is not None and projected_channel != channel:
        raise ScopeConflictError("channel scope conflict")

    fallbacks: list[str] = []
    brand = _token(projection.brand, field_name="brand")
    if brand is None:
        brand = tenant_key
        fallbacks.append("brand<-tenant_key")

    audience = _token(projection.audience, field_name="audience")
    visibility = _token(projection.visibility, field_name="visibility")
    shareability = _token(projection.shareability, field_name="shareability")
    locale = _locale(projection.locale)

    return ResolvedScope(
        tenant_id=tenant_id,
        tenant_key=tenant_key,
        brand=brand,
        market_id=market_id,
        market=market,
        country=resolved_country,
        channel=channel,
        locale=locale,
        audience=audience,
        visibility=visibility,
        shareability=shareability,
        operation=resolved_operation,
        fallbacks=tuple(fallbacks),
    )


def resolve_legacy_scope(
    *,
    tenant_id: str | None,
    brand_id: str | None,
    country_scope: str | None,
    channel_scope: str | None,
    market_id: int | None,
    channel: str | None,
    locale: str | None,
    audience_scope: str | None,
    visibility: str | None,
    shareability: str | None,
    operation: ProtectedOperation,
    tenant_pk: int | None = None,
    market_key: str | None = None,
) -> ResolvedScope:
    if tenant_pk is None or market_id is None or market_key is None:
        raise UnsafeLegacyScopeError("legacy scope requires explicit relational Tenant and Market mapping")
    legacy_channel = _token(channel_scope, field_name="channel_scope", required=True)
    explicit_channel = _token(channel, field_name="channel", required=True)
    if legacy_channel != explicit_channel:
        raise ScopeConflictError("channel scope conflict")
    return resolve_scope(
        authority=ScopeAuthority(
            tenant_id=tenant_pk,
            tenant_key=tenant_id,
            market_id=market_id,
            market_key=market_key,
            country=country_scope,
            channel=explicit_channel,
        ),
        projection=ScopeProjection(
            brand=brand_id,
            country=country_scope,
            channel=legacy_channel,
            locale=locale,
            audience=audience_scope,
            visibility=visibility,
            shareability=shareability,
        ),
        operation=operation,
    )


__all__ = [
    "RESOLVED_SCOPE_SCHEMA",
    "ProtectedOperation",
    "ResolvedScope",
    "ScopeAuthority",
    "ScopeConflictError",
    "ScopeError",
    "ScopeMissingError",
    "ScopeProjection",
    "UnsafeLegacyScopeError",
    "resolve_legacy_scope",
    "resolve_scope",
]
