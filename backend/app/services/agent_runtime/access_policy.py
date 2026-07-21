from __future__ import annotations

import os
from dataclasses import dataclass

from ..webchat_ai_decision_runtime.tool_registry import get_tool_contract
from .tool_adapter import executable_tool_names


_DEFAULT_PUBLIC_WEBCHAT_TOOLS = frozenset(
    {
        "knowledge.search",
        "support.availability",
        "speedaf.order.query",
        "speedaf.express.track.query",
        "speedaf.order.waybillCode.query",
        "handoff.request.create",
    }
)

# This is an explicit compile-time principal policy, not a projection from Tool
# contracts. Production may replace it through the separate permissions setting.
_DEFAULT_PUBLIC_WEBCHAT_PERMISSIONS = frozenset(
    {
        "knowledge:read",
        "webchat:handoff:create",
        "speedaf:tracking:read",
    }
)


@dataclass(frozen=True)
class WebchatAgentAccessPolicy:
    allowed_tools: tuple[str, ...]
    granted_permissions: frozenset[str]
    actor_capabilities: frozenset[str]


def resolve_webchat_agent_access() -> WebchatAgentAccessPolicy:
    requested_tools = _configured_set(
        "WEBCHAT_AGENT_ALLOWED_TOOLS",
        _DEFAULT_PUBLIC_WEBCHAT_TOOLS,
    )
    granted_permissions = _configured_set(
        "WEBCHAT_AGENT_GRANTED_PERMISSIONS",
        _DEFAULT_PUBLIC_WEBCHAT_PERMISSIONS,
    )
    executable = set(executable_tool_names())
    allowed = []
    for name in sorted(requested_tools & executable):
        contract = get_tool_contract(name)
        if contract is None:
            continue
        # Public WebChat does not carry a server-issued confirmation artifact
        # into Agent execution. Confirmation-required Tools remain available to
        # controlled callers, but must never be exposed to this principal.
        if contract.confirmation_required:
            continue
        if not set(contract.required_permissions).issubset(granted_permissions):
            continue
        allowed.append(name)
    return WebchatAgentAccessPolicy(
        allowed_tools=tuple(allowed),
        granted_permissions=frozenset(granted_permissions),
        actor_capabilities=frozenset(granted_permissions),
    )


def _configured_set(name: str, default: frozenset[str]) -> frozenset[str]:
    raw = os.getenv(name)
    if raw is None:
        return default
    return frozenset(
        item.strip()
        for item in raw.split(",")
        if item.strip()
    )
