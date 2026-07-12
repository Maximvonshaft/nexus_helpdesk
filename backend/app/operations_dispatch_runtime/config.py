from __future__ import annotations

from dataclasses import dataclass

_ALLOWED_MODES = {"disabled", "enabled"}
_SAFE_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789._:-")


def _safe_name(value: object, *, field: str, limit: int = 80) -> str:
    resolved = str(value or "").strip().lower()
    if not resolved or len(resolved) > limit or resolved[0] not in "abcdefghijklmnopqrstuvwxyz0123456789":
        raise ValueError(f"operations_dispatch_{field}_invalid")
    if any(char not in _SAFE_NAME_CHARS for char in resolved):
        raise ValueError(f"operations_dispatch_{field}_invalid")
    return resolved


@dataclass(frozen=True)
class OperationsDispatchRuntimeConfig:
    """Explicit runtime configuration.

    The runtime does not read environment variables directly. A deployment or
    caller must provide normalized values. The default is inert and cannot claim
    or mutate Outbox rows.
    """

    mode: str = "disabled"
    adapter_name: str = "disabled"
    app_env: str = "development"
    tenant_authority_ready: bool = False
    batch_size: int = 50
    lease_seconds: int = 120
    heartbeat_interval_seconds: int = 30
    idle_sleep_seconds: float = 2.0

    def validated(self) -> "OperationsDispatchRuntimeConfig":
        mode = _safe_name(self.mode, field="mode", limit=20)
        adapter = _safe_name(self.adapter_name, field="adapter", limit=80)
        app_env = _safe_name(self.app_env, field="app_env", limit=20)
        if mode not in _ALLOWED_MODES:
            raise ValueError("operations_dispatch_mode_unsupported")
        if mode == "disabled" and adapter != "disabled":
            raise ValueError("operations_dispatch_disabled_adapter_mismatch")
        if mode == "enabled" and adapter == "disabled":
            raise ValueError("operations_dispatch_enabled_adapter_missing")
        if not isinstance(self.tenant_authority_ready, bool):
            raise ValueError("operations_dispatch_tenant_authority_invalid")
        if isinstance(self.batch_size, bool) or not 1 <= int(self.batch_size) <= 200:
            raise ValueError("operations_dispatch_batch_size_invalid")
        if isinstance(self.lease_seconds, bool) or not 10 <= int(self.lease_seconds) <= 3600:
            raise ValueError("operations_dispatch_lease_seconds_invalid")
        if isinstance(self.heartbeat_interval_seconds, bool) or not 5 <= int(self.heartbeat_interval_seconds) <= 300:
            raise ValueError("operations_dispatch_heartbeat_interval_invalid")
        idle = float(self.idle_sleep_seconds)
        if not 0.1 <= idle <= 60.0:
            raise ValueError("operations_dispatch_idle_sleep_invalid")
        return OperationsDispatchRuntimeConfig(
            mode=mode,
            adapter_name=adapter,
            app_env=app_env,
            tenant_authority_ready=self.tenant_authority_ready,
            batch_size=int(self.batch_size),
            lease_seconds=int(self.lease_seconds),
            heartbeat_interval_seconds=int(self.heartbeat_interval_seconds),
            idle_sleep_seconds=idle,
        )

    @property
    def execution_enabled(self) -> bool:
        validated = self.validated()
        return validated.mode == "enabled" and validated.tenant_authority_ready
