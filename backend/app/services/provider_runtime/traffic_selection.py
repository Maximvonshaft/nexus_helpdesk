from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .schemas import ProviderRequest


TRAFFIC_SELECTION_SCHEMA = "nexus.provider_runtime.traffic_selection.v1"
TRAFFIC_MODE_ENV = "PROVIDER_RUNTIME_TRAFFIC_MODE"
_VALID_CONFIGURED_MODES = frozenset({"canary", "control", "shadow"})
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


class ProviderTrafficPath(StrEnum):
    CONTROL = "control"
    CANARY_AUTHORITATIVE = "canary_authoritative"
    SHADOW_ONLY = "shadow_only"
    KILL_SWITCH = "kill_switch"


@dataclass(frozen=True)
class ProviderTrafficSelection:
    configured_mode: str
    path: ProviderTrafficPath
    canary_percent: int
    bucket: int | None
    execute_candidate: bool
    authoritative: bool
    reason: str
    schema_version: str = TRAFFIC_SELECTION_SCHEMA

    def safe_summary(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "configured_mode": self.configured_mode,
            "path": self.path.value,
            "canary_percent": self.canary_percent,
            "bucket": self.bucket,
            "execute_candidate": self.execute_candidate,
            "authoritative": self.authoritative,
            "reason": self.reason,
            "bucket_contract": "sha256(tenant,channel,session,scenario)%100",
        }


def configured_traffic_mode(value: str | None = None) -> str:
    raw = value if value is not None else os.getenv(TRAFFIC_MODE_ENV, "canary")
    mode = str(raw or "canary").strip().lower()
    if mode not in _VALID_CONFIGURED_MODES:
        raise ValueError("provider_runtime_traffic_mode_invalid")
    return mode


def _validated_canary_percent(value: Any) -> int:
    try:
        percent = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("provider_runtime_canary_percent_invalid") from exc
    if isinstance(value, float) and not value.is_integer():
        raise ValueError("provider_runtime_canary_percent_invalid")
    if isinstance(value, str) and value.strip() != str(percent):
        raise ValueError("provider_runtime_canary_percent_invalid")
    if not 0 <= percent <= 100:
        raise ValueError("provider_runtime_canary_percent_invalid")
    return percent


def effective_canary_percent(default: int) -> int:
    raw = os.getenv("PROVIDER_RUNTIME_CANARY_PERCENT")
    return _validated_canary_percent(default if raw is None else raw.strip())


def effective_kill_switch(default: bool) -> bool:
    raw = os.getenv("PROVIDER_RUNTIME_KILL_SWITCH")
    if raw is None:
        return bool(default)
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError("provider_runtime_kill_switch_invalid")


def safe_traffic_configuration(
    *,
    default_canary_percent: int = 100,
    default_kill_switch: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    try:
        mode = configured_traffic_mode()
    except ValueError as exc:
        mode = "invalid"
        errors.append(str(exc))
    try:
        canary_percent: int | None = effective_canary_percent(default_canary_percent)
    except ValueError as exc:
        canary_percent = None
        errors.append(str(exc))
    try:
        kill_switch: bool | None = effective_kill_switch(default_kill_switch)
    except ValueError as exc:
        kill_switch = None
        errors.append(str(exc))
    return {
        "schema_version": TRAFFIC_SELECTION_SCHEMA,
        "configured_mode": mode,
        "configuration_errors": errors,
        "canary_percent": canary_percent,
        "canary_percent_env_override": os.getenv("PROVIDER_RUNTIME_CANARY_PERCENT") is not None,
        "kill_switch": kill_switch,
        "kill_switch_env_override": os.getenv("PROVIDER_RUNTIME_KILL_SWITCH") is not None,
        "bucket_contract": "sha256(tenant,channel,session,scenario)%100",
        "authoritative_rule": "configured_mode=canary and bucket<canary_percent",
    }


def stable_canary_bucket(request: ProviderRequest) -> int:
    identity = "\x1f".join(
        (
            str(request.tenant_id or ""),
            str(request.tenant_key or ""),
            str(request.channel_key or ""),
            str(request.session_id or ""),
            str(request.scenario or ""),
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8", errors="replace")).digest()
    return int.from_bytes(digest[:8], "big", signed=False) % 100


def select_provider_traffic(
    request: ProviderRequest,
    *,
    canary_percent: int,
    kill_switch: bool,
    configured_mode_value: str | None = None,
) -> ProviderTrafficSelection:
    percent = _validated_canary_percent(canary_percent)
    mode = configured_traffic_mode(configured_mode_value)

    if kill_switch:
        return ProviderTrafficSelection(
            configured_mode=mode,
            path=ProviderTrafficPath.KILL_SWITCH,
            canary_percent=percent,
            bucket=None,
            execute_candidate=False,
            authoritative=False,
            reason="kill_switch_active",
        )

    bucket = stable_canary_bucket(request)

    if mode == "control":
        return ProviderTrafficSelection(
            configured_mode=mode,
            path=ProviderTrafficPath.CONTROL,
            canary_percent=percent,
            bucket=bucket,
            execute_candidate=False,
            authoritative=False,
            reason="control_mode_configured",
        )

    if mode == "shadow":
        return ProviderTrafficSelection(
            configured_mode=mode,
            path=ProviderTrafficPath.SHADOW_ONLY,
            canary_percent=percent,
            bucket=bucket,
            execute_candidate=True,
            authoritative=False,
            reason="shadow_mode_configured",
        )

    if percent == 0:
        return ProviderTrafficSelection(
            configured_mode=mode,
            path=ProviderTrafficPath.CONTROL,
            canary_percent=percent,
            bucket=bucket,
            execute_candidate=False,
            authoritative=False,
            reason="canary_percent_zero",
        )

    if bucket < percent:
        return ProviderTrafficSelection(
            configured_mode=mode,
            path=ProviderTrafficPath.CANARY_AUTHORITATIVE,
            canary_percent=percent,
            bucket=bucket,
            execute_candidate=True,
            authoritative=True,
            reason="bucket_selected",
        )

    return ProviderTrafficSelection(
        configured_mode=mode,
        path=ProviderTrafficPath.CONTROL,
        canary_percent=percent,
        bucket=bucket,
        execute_candidate=False,
        authoritative=False,
        reason="bucket_not_selected",
    )
