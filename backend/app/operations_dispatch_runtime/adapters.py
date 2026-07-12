from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from ..services.nexus_osr.operations_dispatch_processor import (
    DisabledOperationsDispatchAdapter,
    OperationsDispatchAdapter,
    OperationsDispatchAdapterResult,
    OperationsDispatchEnvelope,
)
from .config import OperationsDispatchRuntimeConfig

_SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_ACK_SCHEMA = "nexus.operations_dispatch.ack.v1"


class AdapterResolutionError(RuntimeError):
    """Raised when runtime configuration cannot resolve an allowed adapter."""


@dataclass(frozen=True)
class RegisteredAdapter:
    name: str
    factory: Callable[[], OperationsDispatchAdapter]
    allowed_environments: frozenset[str]


class AdapterRegistry:
    """Explicit Adapter Registry with a disabled-only production baseline."""

    def __init__(self, adapters: tuple[RegisteredAdapter, ...] | None = None):
        entries = adapters or (
            RegisteredAdapter(
                name="disabled",
                factory=DisabledOperationsDispatchAdapter,
                allowed_environments=frozenset({"development", "test", "staging", "production"}),
            ),
        )
        self._entries: dict[str, RegisteredAdapter] = {}
        for entry in entries:
            name = str(entry.name or "").strip().lower()
            if not name or name in self._entries:
                raise ValueError("operations_dispatch_adapter_registry_invalid")
            if not entry.allowed_environments:
                raise ValueError("operations_dispatch_adapter_environment_missing")
            self._entries[name] = RegisteredAdapter(
                name=name,
                factory=entry.factory,
                allowed_environments=frozenset(
                    str(item or "").strip().lower() for item in entry.allowed_environments
                ),
            )

    def resolve(self, config: OperationsDispatchRuntimeConfig) -> OperationsDispatchAdapter:
        resolved = config.validated()
        entry = self._entries.get(resolved.adapter_name)
        if entry is None:
            raise AdapterResolutionError("operations_dispatch_adapter_not_registered")
        if resolved.app_env not in entry.allowed_environments:
            raise AdapterResolutionError("operations_dispatch_adapter_environment_forbidden")
        adapter = entry.factory()
        if not hasattr(adapter, "dispatch"):
            raise AdapterResolutionError("operations_dispatch_adapter_contract_invalid")
        return adapter

    def safe_inventory(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "name": name,
                "allowed_environments": sorted(entry.allowed_environments),
            }
            for name, entry in sorted(self._entries.items())
        )


class AcknowledgementValidatingAdapter:
    """Require a Provider acceptance receipt bound to the stable dispatch key."""

    def __init__(self, inner: OperationsDispatchAdapter):
        self._inner = inner

    def dispatch(self, envelope: OperationsDispatchEnvelope) -> OperationsDispatchAdapterResult:
        if not envelope.tenant_key or envelope.tenant_key == "default":
            return _invalid("tenant_authority_unavailable")
        result = self._inner.dispatch(envelope)
        if not result.success:
            return result
        try:
            acknowledgement, receipt_id = validate_acknowledgement(
                result.acknowledgement,
                dispatch_key=envelope.dispatch_key,
            )
        except ValueError as exc:
            return _invalid(str(exc))
        return OperationsDispatchAdapterResult(
            success=True,
            retryable=False,
            acknowledgement=acknowledgement,
            external_reference=receipt_id,
        )


def validate_acknowledgement(
    value: object,
    *,
    dispatch_key: str,
) -> tuple[dict[str, object], str]:
    if not isinstance(value, Mapping):
        raise ValueError("provider_ack_not_object")
    allowed_keys = {"schema", "accepted", "dispatch_key", "provider", "receipt_id"}
    if set(value) - allowed_keys:
        raise ValueError("provider_ack_unknown_field")
    if value.get("schema") != _ACK_SCHEMA:
        raise ValueError("provider_ack_schema_invalid")
    if value.get("accepted") is not True:
        raise ValueError("provider_ack_not_accepted")
    if value.get("dispatch_key") != dispatch_key:
        raise ValueError("provider_ack_dispatch_key_mismatch")
    provider = _safe_value(value.get("provider"), field="provider", limit=80)
    receipt_id = _safe_value(value.get("receipt_id"), field="receipt", limit=160)
    return (
        {
            "schema": _ACK_SCHEMA,
            "accepted": True,
            "dispatch_key": dispatch_key,
            "provider": provider,
            "receipt_id": receipt_id,
        },
        receipt_id,
    )


def _safe_value(value: object, *, field: str, limit: int) -> str:
    resolved = str(value or "").strip()
    if len(resolved) > limit or not _SAFE_VALUE_RE.fullmatch(resolved):
        raise ValueError(f"provider_ack_{field}_invalid")
    return resolved


def _invalid(reason: str) -> OperationsDispatchAdapterResult:
    return OperationsDispatchAdapterResult(
        success=False,
        retryable=False,
        error_category="provider_ack_invalid",
        error_summary=reason[:120],
    )
