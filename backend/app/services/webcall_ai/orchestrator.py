from __future__ import annotations

import re
from dataclasses import dataclass

from ...services.tracking_fact_schema import TrackingFactResult, hash_tracking_number
from ...services.tracking_fact_service import extract_tracking_number, lookup_tracking_fact
from ...voice_models import WebchatVoiceSession
from .config import WebCallAISettings, get_webcall_ai_settings
from .reply_builder import (
    build_handoff_reply,
    build_missing_tracking_reply,
    build_tracking_lookup_disabled_reply,
    build_tracking_reply,
)

READ_ONLY_TRACKING_TOOL_NAME = "speedaf.order.query"

_TRACKING_WORDS = re.compile(r"\b(track|tracking|parcel|package|where|status|shipment|delivery|waybill)\b", re.I)
_HANDOFF_WORDS = re.compile(
    r"\b(refund|compensation|cancel|change address|address change|driver|dsp|lawyer|legal|privacy|angry|complaint)\b",
    re.I,
)


@dataclass(frozen=True)
class WebCallAIOrchestratorResult:
    action: str
    intent: str
    ai_response_text_redacted: str
    handoff_required: bool
    handoff_reason: str | None
    tracking_number_hash: str | None
    tracking_number_suffix: str | None
    tracking_fact_ok: bool
    tracking_tool_status: str | None
    tracking_failure_reason: str | None
    speedaf_tool_name: str | None
    nexus_decision: str
    decision_reason: str
    result_status: str


def run_webcall_ai_orchestrator(
    *,
    customer_text_redacted: str | None,
    session: WebchatVoiceSession,
    worker_id: str,
    settings: WebCallAISettings | None = None,
) -> WebCallAIOrchestratorResult:
    resolved = settings or get_webcall_ai_settings()
    text = customer_text_redacted or ""
    if _HANDOFF_WORDS.search(text):
        return WebCallAIOrchestratorResult(
            action="handoff_to_human",
            intent="handoff_high_risk",
            ai_response_text_redacted=build_handoff_reply(),
            handoff_required=True,
            handoff_reason="high_risk_request",
            tracking_number_hash=None,
            tracking_number_suffix=None,
            tracking_fact_ok=False,
            tracking_tool_status=None,
            tracking_failure_reason=None,
            speedaf_tool_name=None,
            nexus_decision="handoff",
            decision_reason="high_risk_request_requires_human",
            result_status="handoff_required",
        )

    tracking_number = extract_tracking_number(text)
    tracking_hash = hash_tracking_number(tracking_number)
    tracking_suffix = tracking_number[-4:] if tracking_number else None
    if not tracking_number:
        intent = "tracking_missing_number" if _TRACKING_WORDS.search(text) else "unknown_missing_tracking_number"
        return WebCallAIOrchestratorResult(
            action="ask_tracking_number",
            intent=intent,
            ai_response_text_redacted=build_missing_tracking_reply(),
            handoff_required=False,
            handoff_reason=None,
            tracking_number_hash=None,
            tracking_number_suffix=None,
            tracking_fact_ok=False,
            tracking_tool_status=None,
            tracking_failure_reason="missing_tracking_number",
            speedaf_tool_name=None,
            nexus_decision="allowed",
            decision_reason="tracking_number_required_for_lookup",
            result_status="tracking_number_requested",
        )

    if not resolved.tracking_lookup_enabled:
        return WebCallAIOrchestratorResult(
            action="handoff_to_human",
            intent="tracking_lookup_unavailable",
            ai_response_text_redacted=build_tracking_lookup_disabled_reply(),
            handoff_required=True,
            handoff_reason="tracking_lookup_disabled",
            tracking_number_hash=tracking_hash,
            tracking_number_suffix=tracking_suffix,
            tracking_fact_ok=False,
            tracking_tool_status="disabled",
            tracking_failure_reason="tracking_lookup_disabled",
            speedaf_tool_name=None,
            nexus_decision="handoff",
            decision_reason="tracking_lookup_disabled",
            result_status="tracking_lookup_disabled",
        )

    try:
        fact = lookup_tracking_fact(
            tracking_number=tracking_number,
            conversation_id=session.conversation_id,
            ticket_id=session.ticket_id,
            request_id=f"webcall-ai-{session.public_id}-{worker_id}",
            country_code=resolved.tracking_country_code,
        )
    except Exception:
        fact = TrackingFactResult(
            ok=False,
            tracking_number=tracking_number,
            tool_status="error",
            pii_redacted=True,
            failure_reason="tracking_lookup_error",
        )

    if fact.failure_reason == "multiple_waybill_candidates":
        return _result_from_fact(
            fact=fact,
            action="ask_waybill_suffix_selection",
            intent="tracking_multiple_candidates",
            tracking_hash=tracking_hash,
            tracking_suffix=tracking_suffix,
            nexus_decision="allowed",
            decision_reason="safe_suffix_selection_required",
            result_status="waybill_suffix_requested",
        )
    if fact.ok and fact.fact_evidence_present:
        return _result_from_fact(
            fact=fact,
            action="explain_tracking_fact",
            intent="tracking_status_lookup",
            tracking_hash=tracking_hash,
            tracking_suffix=tracking_suffix,
            nexus_decision="allowed",
            decision_reason="read_only_tracking_fact_available",
            result_status="tracking_fact_explained",
        )
    return _result_from_fact(
        fact=fact,
        action="handoff_to_human",
        intent="tracking_lookup_failed",
        tracking_hash=tracking_hash,
        tracking_suffix=tracking_suffix,
        nexus_decision="handoff",
        decision_reason="trusted_tracking_lookup_unavailable",
        result_status="tracking_lookup_failed",
        handoff_required=True,
        handoff_reason="tracking_lookup_failed",
    )


def _result_from_fact(
    *,
    fact: TrackingFactResult,
    action: str,
    intent: str,
    tracking_hash: str | None,
    tracking_suffix: str | None,
    nexus_decision: str,
    decision_reason: str,
    result_status: str,
    handoff_required: bool = False,
    handoff_reason: str | None = None,
) -> WebCallAIOrchestratorResult:
    return WebCallAIOrchestratorResult(
        action=action,
        intent=intent,
        ai_response_text_redacted=build_tracking_reply(fact),
        handoff_required=handoff_required,
        handoff_reason=handoff_reason,
        tracking_number_hash=tracking_hash,
        tracking_number_suffix=tracking_suffix,
        tracking_fact_ok=fact.ok,
        tracking_tool_status=fact.tool_status,
        tracking_failure_reason=fact.failure_reason,
        speedaf_tool_name=READ_ONLY_TRACKING_TOOL_NAME,
        nexus_decision=nexus_decision,
        decision_reason=decision_reason,
        result_status=result_status,
    )
