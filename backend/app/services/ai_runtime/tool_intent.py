from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_ALLOWED_TOOL_INTENTS = {"create_ticket", "enqueue_handoff_snapshot", "tracking_lookup"}


@dataclass(frozen=True)
class ToolIntent:
    name: str
    reason: str | None = None
    arguments: dict[str, Any] | None = None
    confidence: float | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "reason": self.reason,
            "arguments": self.arguments or {},
            "confidence": self.confidence,
        }


def normalize_tool_intents(value: Any) -> list[ToolIntent]:
    """Normalize future strict-JSON tool intent hints.

    Phase 1 intentionally does not execute tools from model output. The schema is
    added so later phases can extend strict JSON without enabling native provider
    tool/function calls or direct database writes.
    """

    if not isinstance(value, list):
        return []
    intents: list[ToolIntent] = []
    for item in value[:5]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name not in _ALLOWED_TOOL_INTENTS:
            continue
        reason = item.get("reason")
        if reason is not None and not isinstance(reason, str):
            reason = str(reason)
        arguments = item.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        confidence = item.get("confidence")
        if not isinstance(confidence, (int, float)):
            confidence = None
        intents.append(
            ToolIntent(
                name=name,
                reason=(reason or "").strip()[:240] or None,
                arguments=arguments,
                confidence=float(confidence) if confidence is not None else None,
            )
        )
    return intents
