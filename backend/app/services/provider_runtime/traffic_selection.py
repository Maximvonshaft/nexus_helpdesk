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
_BUCKET_CONTRACT = "sha256(tenant_id,tenant_key,channel_key,session_id,scenario)%100"
_TRAFFIC_MODE_INVALID = "provider_runtime_traffic_mode_invalid"
_CANARY_PERCENT_INVALID = "provider_runtime_canary_percent_invalid"
_KILL_SWITCH_INVALID = "provider_runtime_kill_switch_invalid"
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
    configuration_errors: tuple[str, ...] = ()
    schema_version: str = TRAFFIC_SELECTION_SCHEMA

    def safe_summary(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "configured_mode": self.configured_mode,
            "configuration_errors": list(self.configuration_errors),
            "path": self.path.value,
            "canary_percent": self.canary_percent,
            "allowed_canary_percents": sorted(ALLOWED_CANARY_PERCENTS),
            "bucket": self.bucket,
            "execute_candidate": self.execute_candidate,
            "authoritative": self.authoritative,
            "reason": self.reason,
            "bucket_contract": _BUCKET_CONTRACT,
        }


def configured_traffic_mode(value: str | None = None) -> str:
    if value is None:
        configured = os.getenv(TRAFFIC_MODE_ENV)
        raw = "control" if configured is None else configured
    else:
        raw = value
    mode = str(raw).strip().lower()
    if mode not in _VALID_CONFIGURED_MODES:
        raise ValueError(_TRAFFIC_MODE_INVALID)
    return mode


def _validated_canary_percent(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError(_CANARY_PERCENT_INVALID)
    try:
        percent = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(_CANARY_PERCENT_INVALID) from exc
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(_CANARY_PERCENT_INVALID)
    if isinstance(value, str) and value.strip() != str(percent):
        raise ValueError(_CANARY_PERCENT_INVALID)
    if percent not in ALLOWED_CANARY_PERCENTS:
        raise ValueError(_CANARY_PERCENT_INVALID)
    return percent


def _validated_kill_switch(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(_KILL_SWITCH_INVALID)
    return value


def effective_canary_percent(default: int) -> int:
    raw = os.getenv("PROVIDER_RUNTIME_CANARY_PERCENT")
    return _validated_canary_percent(default if raw is None else raw.strip())


def effective_kill_switch(default: bool) -> bool:
    raw = os.getenv("PROVIDER_RUNTIME_KILL_SWITCH")
    if raw is None:
        return _validated_kill_switch(default)
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(_KILL_SWITCH_INVALID)


def _append_unique(errors: list[str], error_code: str) -> None:
    if error_code not in errors:
        errors.append(error_code)


def persisted_traffic_configuration_errors(
    *,
    canary_percent: Any,
    kill_switch: Any,
) -> list[str]:
    errors: list[str] = []
    try:
        _validated_canary_percent(canary_percent)
    except ValueError:
        _append_unique(errors, _CANARY_PERCENT_INVALID)
    try:
        _validated_kill_switch(kill_switch)
    except ValueError:
        _append_unique(errors, _KILL_SWITCH_INVALID)
    return errors


def safe_traffic_configuration(
    *,
    default_canary_percent: int = 0,
    default_kill_switch: bool = False,
) -> dict[str, Any]:
    errors = persisted_traffic_configuration_errors(
        canary_percent=default_canary_percent,
        kill_switch=default_kill_switch,
    )

    try:
        normalized_default_canary: int | None = _validated_canary_percent(default_canary_percent)
    except ValueError:
        normalized_default_canary = None

    try:
        normalized_default_kill_switch: bool | None = _validated_kill_switch(default_kill_switch)
    except ValueError:
        normalized_default_kill_switch = None

    try:
        mode = configured_traffic_mode()
    except ValueError:
        mode = "invalid"
        _append_unique(errors, _TRAFFIC_MODE_INVALID)

    canary_override = os.getenv("PROVIDER_RUNTIME_CANARY_PERCENT")
    if canary_override is None:
        canary_percent = normalized_default_canary
    else:
        try:
            canary_percent = _validated_canary_percent(canary_override.strip())
        except ValueError:
            canary_percent = None
            _append_unique(errors, _CANARY_PERCENT_INVALID)

    kill_switch_override = os.getenv("PROVIDER_RUNTIME_KILL_SWITCH")
    if kill_switch_override is None:
        kill_switch = normalized_default_kill_switch
    else:
        try:
            kill_switch = effective_kill_switch(False)
        except ValueError:
            kill_switch = None
            _append_unique(errors, _KILL_SWITCH_INVALID)

    return {
        "schema_version": TRAFFIC_SELECTION_SCHEMA,
        "configured_mode": mode,
        "configuration_errors": errors,
        "default_canary_percent": normalized_default_canary,
        "default_kill_switch": normalized_default_kill_switch,
        "canary_percent": canary_percent,
        "allowed_canary_percents": sorted(ALLOWED_CANARY_PERCENTS),
        "canary_percent_env_override": canary_override is not None,
        "kill_switch": kill_switch,
        "kill_switch_env_override": kill_switch_override is not None,
        "bucket_contract": _BUCKET_CONTRACT,
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
    normalized_kill_switch = _validated_kill_switch(kill_switch)

    if normalized_kill_switch:
        errors: list[str] = []
        try:
            percent = _validated_canary_percent(canary_percent)
        except ValueError:
            percent = 0
            _append_unique(errors, _CANARY_PERCENT_INVALID)
        try:
            mode = configured_traffic_mode(configured_mode_value)
        except ValueError:
            mode = "invalid"
            _append_unique(errors, _TRAFFIC_MODE_INVALID)
        return ProviderTrafficSelection(
            configured_mode=mode,
            path=ProviderTrafficPath.KILL_SWITCH,
            canary_percent=percent,
            bucket=None,
            execute_candidate=False,
            authoritative=False,
            reason="kill_switch_active",
            configuration_errors=tuple(errors),
        )

    percent = _validated_canary_percent(canary_percent)
    mode = configured_traffic_mode(configured_mode_value)
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
