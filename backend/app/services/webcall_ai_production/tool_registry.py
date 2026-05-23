from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .config import get_webcall_ai_production_settings


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    risk_level: str
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def call(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        spec = self._tools.get(name)
        if spec is None:
            return {"ok": False, "decision": "blocked", "reason": "tool_not_allowlisted"}
        if spec.risk_level != "read_only":
            settings = get_webcall_ai_production_settings()
            if spec.name == "speedaf_work_order" and not settings.allow_speedaf_work_order:
                return {"ok": False, "decision": "blocked", "reason": "work_order_disabled"}
            if spec.name == "cancel_order" and not settings.allow_cancel:
                return {"ok": False, "decision": "blocked", "reason": "cancel_disabled"}
            if spec.name == "address_update" and not settings.allow_address_update:
                return {"ok": False, "decision": "blocked", "reason": "address_update_disabled"}
        result = spec.handler(payload)
        return {"ok": True, "decision": "allowed", "tool": name, "result": result}


def default_registry() -> ToolRegistry:
    from .tools.tracking_lookup import lookup_tracking

    registry = ToolRegistry()
    registry.register(ToolSpec(name="tracking_lookup", risk_level="read_only", handler=lookup_tracking))
    return registry

