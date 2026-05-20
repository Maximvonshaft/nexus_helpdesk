from typing import Any, List, Optional
from pydantic import BaseModel, Field

class ProviderCapabilities(BaseModel):
    fast_reply: bool = False
    structured_output: bool = False
    streaming: bool = False
    tool_execution: bool = False
    ticket_action: bool = False
    handoff_decision: bool = False
    supports_tracking_context: bool = False
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
    recent_context: Optional[Any] = None
    tracking_fact_summary: Optional[str] = None
    tracking_fact_evidence_present: bool = False
    output_contract: str
    timeout_ms: int
    metadata: Optional[dict] = Field(default_factory=dict)

class ProviderResult(BaseModel):
    ok: bool
    provider: str
    model: Optional[str] = None
    elapsed_ms: int
    raw_payload_safe_summary: Optional[dict] = None
    structured_output: Optional[dict] = None
    error_code: Optional[str] = None
    retryable: bool = False
    fallback_allowed: bool = True

    @classmethod
    def unavailable(cls, provider: str, error_code: str, elapsed_ms: int, fallback_allowed: bool = True) -> 'ProviderResult':
        return cls(
            ok=False,
            provider=provider,
            elapsed_ms=elapsed_ms,
            error_code=error_code,
            retryable=False,
            fallback_allowed=fallback_allowed,
            raw_payload_safe_summary={"unavailable": True}
        )
