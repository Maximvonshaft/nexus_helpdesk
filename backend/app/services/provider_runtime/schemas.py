from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProviderCapabilities(BaseModel):
    agent_turn: bool = False
    structured_output: bool = False
    streaming: bool = False
    tool_execution: bool = False
    handoff_decision: bool = False
    supports_vision: bool = False
    max_timeout_ms: int = 30000
    safety_level: str = "standard"


class ProviderRequest(BaseModel):
    request_id: str
    tenant_id: str
    tenant_key: str
    channel_key: str
    session_id: str
    scenario: str
    body: Any
    recent_context: Any | None = None
    output_contract: str
    timeout_ms: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderResult(BaseModel):
    ok: bool
    provider: str
    raw_provider: str | None = None
    reply_source: str | None = None
    model: str | None = None
    elapsed_ms: int
    raw_payload_safe_summary: dict[str, Any] | None = None
    structured_output: dict[str, Any] | None = None
    error_code: str | None = None
    retryable: bool = False
    fallback_allowed: bool = True

    @classmethod
    def unavailable(
        cls,
        provider: str,
        error_code: str,
        elapsed_ms: int,
        fallback_allowed: bool = True,
    ) -> "ProviderResult":
        return cls(
            ok=False,
            provider=provider,
            raw_provider=provider,
            reply_source=provider,
            elapsed_ms=elapsed_ms,
            error_code=error_code,
            retryable=False,
            fallback_allowed=fallback_allowed,
            raw_payload_safe_summary={"unavailable": True},
        )
