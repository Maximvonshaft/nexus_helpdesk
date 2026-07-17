from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .schemas import ProviderRequest


TRAFFIC_SELECTION_SCHEMA = "nexus.provider_runtime.traffic_selection.v1"
TRAFFIC_MODE_ENV = "PROVIDER_RUNTIME_TRAFFIC_MODE"
ALLOWED_CANARY_PERCENTS = frozenset({0, 1, 5, 25, 100})
_VALID_MODES = frozenset({"control", "canary", "shadow"})
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_BUCKET_CONTRACT = "sha256(tenant_id,tenant_key,channel_key,session_id,scenario)%100"


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
            "bucket_contract": _BUCKET_CONTRACT,
        }


def configured_traffic_mode(
    value: str | None = None,
    *,
    default: str = "control",
) -> str:
    raw = os.getenv(TRAFFIC_MODE_ENV, default) if value is None else value
    mode = str(raw or "").strip().lower()
    if mode not in _VALID_MODES:
        raise ValueError("provider_runtime_traffic_mode_invalid")
    return mode


def validate_canary_percent(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("provider_runtime_canary_percent_invalid")
    try:
        percent = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("provider_runtime_canary_percent_invalid") from exc
    if isinstance(value, float) and not value.is_integer():
        raise ValueError("provider_runtime_canary_percent_invalid")
    if isinstance(value, str) and value.strip() != str(percent):
        raise ValueError("provider_runtime_canary_percent_invalid")
    if percent not in ALLOWED_CANARY_PERCENTS:
        raise ValueError("provider_runtime_canary_percent_invalid")
    return percent


def effective_canary_percent(default: Any) -> int:
    raw = os.getenv("PROVIDER_RUNTIME_CANARY_PERCENT")
    return validate_canary_percent(default if raw is None else raw.strip())


def validate_kill_switch(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raise ValueError("provider_runtime_kill_switch_invalid")


def effective_kill_switch(default: Any) -> bool:
    persisted = validate_kill_switch(default)
    raw = os.getenv("PROVIDER_RUNTIME_KILL_SWITCH")
    if raw is None:
        return persisted
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        # Environment configuration may activate a kill switch, but may not
        # clear a persisted tenant/channel kill switch.
        return persisted
    raise ValueError("provider_runtime_kill_switch_invalid")


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
    canary_percent: Any,
    kill_switch: Any,
    configured_mode_value: str | None = None,
) -> ProviderTrafficSelection:
    normalized_kill_switch = validate_kill_switch(kill_switch)

    if normalized_kill_switch:
        try:
            percent = validate_canary_percent(canary_percent)
        except ValueError:
            percent = 0
        try:
            mode = configured_traffic_mode(
                configured_mode_value,
                default="canary" if percent else "control",
            )
        except ValueError:
            mode = "control"
        return ProviderTrafficSelection(
            configured_mode=mode,
            path=ProviderTrafficPath.KILL_SWITCH,
            canary_percent=percent,
            bucket=None,
            execute_candidate=False,
            authoritative=False,
            reason="kill_switch_active",
        )

    percent = validate_canary_percent(canary_percent)
    mode = configured_traffic_mode(
        configured_mode_value,
        default="canary" if percent else "control",
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

    if percent == 0 or bucket >= percent:
        return ProviderTrafficSelection(
            configured_mode=mode,
            path=ProviderTrafficPath.CONTROL,
            canary_percent=percent,
            bucket=bucket,
            execute_candidate=False,
            authoritative=False,
            reason="traffic_percent_zero" if percent == 0 else "bucket_not_selected",
        )

    if mode == "shadow":
        return ProviderTrafficSelection(
            configured_mode=mode,
            path=ProviderTrafficPath.SHADOW_ONLY,
            canary_percent=percent,
            bucket=bucket,
            execute_candidate=True,
            authoritative=False,
            reason="shadow_bucket_selected",
        )

    return ProviderTrafficSelection(
        configured_mode=mode,
        path=ProviderTrafficPath.CANARY_AUTHORITATIVE,
        canary_percent=percent,
        bucket=bucket,
        execute_candidate=True,
        authoritative=True,
        reason="canary_bucket_selected",
    )
