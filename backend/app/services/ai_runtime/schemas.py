from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .tool_intent import ToolIntent


@dataclass(frozen=True)
class RuntimeAIProviderRequest:
    tenant_key: str
    channel_key: str
    session_id: str
    body: str
    recent_context: list[dict[str, Any]] | None = None
    request_id: str | None = None
    market_id: int | None = None
    language: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class RuntimeAIProviderResult:
    ok: bool
    ai_generated: bool
    reply_source: str | None
    raw_provider: str | None
    raw_payload_safe_summary: dict[str, Any] | None
    reply: str | None
    intent: str | None
    handoff_required: bool
    handoff_reason: str | None
    recommended_agent_action: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_intents: list[ToolIntent] = field(default_factory=list)
    elapsed_ms: int = 0
    error_code: str | None = None
    retry_after_ms: int | None = None

    @classmethod
    def unavailable(
        cls,
        *,
        provider: str,
        error_code: str = "ai_unavailable",
        elapsed_ms: int = 0,
        retry_after_ms: int | None = 1500,
        safe_summary: dict[str, Any] | None = None,
    ) -> "RuntimeAIProviderResult":
        return cls(
            ok=False,
            ai_generated=False,
            reply_source=None,
            raw_provider=provider,
            raw_payload_safe_summary=safe_summary,
            reply=None,
            intent=None,
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            tool_calls=[],
            tool_intents=[],
            elapsed_ms=elapsed_ms,
            error_code=error_code,
            retry_after_ms=retry_after_ms,
        )
