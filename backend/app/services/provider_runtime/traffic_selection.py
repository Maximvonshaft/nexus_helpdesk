from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .schemas import ProviderRequest


TRAFFIC_SELECTION_SCHEMA = "nexus.provider_runtime.traffic_selection.v1"
TRAFFIC_MODE_ENV = "PROVIDER_RUNTIME_TRAFFIC_MODE"
ALLOWED_CANARY_PERCENTAGES = frozenset({0, 1, 5, 25, 100})
_BUCKET_CONTRACT = "sha256(tenant_id,tenant_key,channel_key,session_id,scenario)%100"
_TRAFFIC_MODE_INVALID = "provider_runtime_traffic_mode_invalid"
_CANARY_PERCENT_INVALID = "provider_runtime_canary_percent_invalid"
_KILL_SWITCH_INVALID = "provider_runtime_kill_switch_invalid"
_VALID_CONFIGURED_MODES = frozenset({"control", "canary", "shadow"})
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

    def safe_summary(self, *, fallback_result: str = "not_attempted") -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "configured_mode": self.configured_mode,
            "configuration_errors": list(self.configuration_errors),
            "path": self.path.value,
            "canary_percent": self.canary_percent,
            "bucket": self.bucket,
            "execute_candidate": self.execute_candidate,
            "authoritative": self.authoritative,
            "reason": self.reason,
            "fallback_result": fallback_result,
            "bucket_contract": _BUCKET_CONTRACT,
        }


def _append_unique(errors: list[str], error_code: str) -> None:
    if error_code not in errors:
        errors.append(error_code)


def _validated_mode(value: Any) -> str:
    mode = str(value).strip().lower()
    if mode not in _VALID_CONFIGURED_MODES:
        raise ValueError(_TRAFFIC_MODE_INVALID)
    return mode


def _validated_canary_percent(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError(_CANARY_PERCENT_INVALID)
    if isinstance(value, int):
        percent = value
    elif isinstance(value, str):
        raw = value.strip()
        try:
            percent = int(raw)
        except ValueError as exc:
            raise ValueError(_CANARY_PERCENT_INVALID) from exc
        if raw != str(percent):
            raise ValueError(_CANARY_PERCENT_INVALID)
    else:
        raise ValueError(_CANARY_PERCENT_INVALID)
    if percent not in ALLOWED_CANARY_PERCENTAGES:
        raise ValueError(_CANARY_PERCENT_INVALID)
    return percent


def normalize_persisted_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool) and value in {0, 1}:
        return bool(value)
    raise ValueError(_KILL_SWITCH_INVALID)


def _validated_env_boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(_KILL_SWITCH_INVALID)


def safe_traffic_configuration(
    *,
    default_canary_percent: Any = 0,
    default_kill_switch: Any = False,
    default_mode: Any = "control",
) -> dict[str, Any]:
    errors: list[str] = []

    try:
        normalized_default_percent: int | None = _validated_canary_percent(default_canary_percent)
    except ValueError:
        normalized_default_percent = None
        _append_unique(errors, _CANARY_PERCENT_INVALID)

    try:
        normalized_default_kill_switch: bool | None = normalize_persisted_boolean(default_kill_switch)
    except ValueError:
        normalized_default_kill_switch = None
        _append_unique(errors, _KILL_SWITCH_INVALID)

    try:
        normalized_default_mode: str | None = _validated_mode(default_mode)
    except ValueError:
        normalized_default_mode = None
        _append_unique(errors, _TRAFFIC_MODE_INVALID)

    mode_override = os.getenv(TRAFFIC_MODE_ENV)
    if mode_override is None:
        mode = normalized_default_mode
    else:
        try:
            mode = _validated_mode(mode_override)
        except ValueError:
            mode = None
            _append_unique(errors, _TRAFFIC_MODE_INVALID)

    percent_override = os.getenv("PROVIDER_RUNTIME_CANARY_PERCENT")
    if percent_override is None:
        canary_percent = normalized_default_percent
    else:
        try:
            canary_percent = _validated_canary_percent(percent_override)
        except ValueError:
            canary_percent = None
            _append_unique(errors, _CANARY_PERCENT_INVALID)

    kill_override = os.getenv("PROVIDER_RUNTIME_KILL_SWITCH")
    if kill_override is None:
        kill_switch = normalized_default_kill_switch
    else:
        try:
            kill_switch = _validated_env_boolean(kill_override)
        except ValueError:
            kill_switch = None
            _append_unique(errors, _KILL_SWITCH_INVALID)

    return {
        "schema_version": TRAFFIC_SELECTION_SCHEMA,
        "configured_mode": mode,
        "configuration_errors": errors,
        "default_mode": normalized_default_mode,
        "default_canary_percent": normalized_default_percent,
        "default_kill_switch": normalized_default_kill_switch,
        "canary_percent": canary_percent,
        "canary_percent_env_override": percent_override is not None,
        "kill_switch": kill_switch,
        "kill_switch_env_override": kill_override is not None,
        "traffic_mode_env_override": mode_override is not None,
        "authoritative": False,
        "bucket_contract": _BUCKET_CONTRACT,
        "authorized_canary_percentages": sorted(ALLOWED_CANARY_PERCENTAGES),
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
    canary_percent: Any,
    kill_switch: Any,
    configured_mode_value: Any = "control",
) -> ProviderTrafficSelection:
    configuration = safe_traffic_configuration(
        default_canary_percent=canary_percent,
        default_kill_switch=kill_switch,
        default_mode=configured_mode_value,
    )
    errors = tuple(configuration["configuration_errors"])
    mode = configuration["configured_mode"] or "invalid"
    percent = configuration["canary_percent"]
    normalized_percent = percent if isinstance(percent, int) else 0

    if configuration["kill_switch"] is True:
        return ProviderTrafficSelection(
            configured_mode=mode,
            path=ProviderTrafficPath.KILL_SWITCH,
            canary_percent=normalized_percent,
            bucket=None,
            execute_candidate=False,
            authoritative=False,
            reason="kill_switch_active",
            configuration_errors=errors,
        )

    if errors:
        return ProviderTrafficSelection(
            configured_mode=mode,
            path=ProviderTrafficPath.CONTROL,
            canary_percent=normalized_percent,
            bucket=None,
            execute_candidate=False,
            authoritative=False,
            reason="traffic_configuration_invalid",
            configuration_errors=errors,
        )

    bucket = stable_canary_bucket(request)
    if mode == "control":
        return ProviderTrafficSelection(
            configured_mode=mode,
            path=ProviderTrafficPath.CONTROL,
            canary_percent=normalized_percent,
            bucket=bucket,
            execute_candidate=False,
            authoritative=False,
            reason="control_mode_configured",
        )

    if mode == "shadow":
        return ProviderTrafficSelection(
            configured_mode=mode,
            path=ProviderTrafficPath.SHADOW_ONLY,
            canary_percent=normalized_percent,
            bucket=bucket,
            execute_candidate=True,
            authoritative=False,
            reason="shadow_mode_configured",
        )

    if normalized_percent == 0:
        return ProviderTrafficSelection(
            configured_mode=mode,
            path=ProviderTrafficPath.CONTROL,
            canary_percent=normalized_percent,
            bucket=bucket,
            execute_candidate=False,
            authoritative=False,
            reason="canary_percent_zero",
        )

    if bucket < normalized_percent:
        return ProviderTrafficSelection(
            configured_mode=mode,
            path=ProviderTrafficPath.CANARY_AUTHORITATIVE,
            canary_percent=normalized_percent,
            bucket=bucket,
            execute_candidate=True,
            authoritative=True,
            reason="bucket_selected",
        )

    return ProviderTrafficSelection(
        configured_mode=mode,
        path=ProviderTrafficPath.CONTROL,
        canary_percent=normalized_percent,
        bucket=bucket,
        execute_candidate=False,
        authoritative=False,
        reason="bucket_not_selected",
    )
