from __future__ import annotations

import os
import re
from typing import Any

from pydantic import ValidationError

from app.services.knowledge_prompt_service import summarize_rag_trace
from app.services.tracking_fact_schema import hash_tracking_number
from app.services.tracking_identifier_policy import looks_like_tracking_identifier
from app.services.webchat_runtime_output_parser import RuntimeReplyParseError, assert_customer_visible_reply_is_safe

from .audit import log_ai_decision_audit
from .policy_gate import PolicyGateResult, validate_ai_decision
from .schemas import AI_DECISION_SCHEMA_VERSION, AIDecision, AIDecisionEvidence, AIDecisionToolCall, normalize_intent, normalize_next_action, normalize_risk_level
from .tool_registry import canonical_tool_name


def _clip(value: Any, limit: int) -> str | None:
    cleaned = " ".join(str(value or "").strip().split())
    return cleaned[:limit] if cleaned else None


def _trusted_tracking_fact_present(metadata: dict[str, Any] | None) -> bool:
    if not isinstance(metadata, dict) or not metadata.get("pii_redacted"):
        return False
    return bool(metadata.get("fact_evidence_present") or metadata.get("tool_status") == "success")


def _trusted_tracking_hash(*, metadata: dict[str, Any] | None, tracking_number: str | None) -> str | None:
    metadata_hash = metadata.get("tracking_number_hash") if isinstance(metadata, dict) else None
    if isinstance(metadata_hash, str) and metadata_hash.startswith("sha256:"):
        return metadata_hash
    if tracking_number:
        return hash_tracking_number(tracking_number)
    return None


def _tracking_suffix(tracking_number: str | None) -> str | None:
    cleaned = "".join(ch for ch in str(tracking_number or "").strip().upper() if ch.isalnum())
    return cleaned[-6:] if cleaned else None


def _safe_tracking_control_value(value: Any, *, fallback_tracking_number: str | None = None) -> str | None:
    cleaned = _clip(value, 120)
    if not cleaned:
        return _clip(fallback_tracking_number, 120)
    lowered = cleaned.lower()
    if (
        "parcel ending" in lowered
        or "tracking number ending" in lowered
        or "tracking_reference_ending" in lowered
        or "tracking number parcel ending" in lowered
        or "运单尾号" in cleaned
        or "单号尾号" in cleaned
    ):
        return _clip(fallback_tracking_number, 120)
    compact = "".join(ch for ch in cleaned.upper() if ch.isalnum())
    if " " in cleaned.strip() and not compact.startswith(("CH", "SP", "SF")):
        return _clip(fallback_tracking_number, 120)
    if not looks_like_tracking_identifier(cleaned):
        return _clip(fallback_tracking_number, 120)
    return cleaned


def _polish_tracking_reference(reply: str, *, suffix: str) -> str:
    suffix_pattern = re.escape(suffix)
    polished = reply
    replacements = [
        (rf"\b[Yy]our\s+tracking\s+number\s+tracking\s+number\s+ending\s+({suffix_pattern})\b", rf"Your parcel ending \1"),
        (rf"\btracking\s+number\s+tracking\s+number\s+ending\s+({suffix_pattern})\b", rf"parcel ending \1"),
        (rf"\b[Yy]our\s+tracking\s+number\s+ending\s+({suffix_pattern})\b", rf"Your parcel ending \1"),
        (rf"\btracking\s+number\s+ending\s+({suffix_pattern})\b", rf"parcel ending \1"),
        (rf"\b[Yy]our\s+tracking\s+number\s+parcel\s+ending\s+({suffix_pattern})\b", rf"Your parcel ending \1"),
        (rf"\btracking\s+number\s+parcel\s+ending\s+({suffix_pattern})\b", rf"parcel ending \1"),
        (rf"\b[Yy]our\s+parcel\s+parcel\s+ending\s+({suffix_pattern})\b", rf"Your parcel ending \1"),
        (rf"\b[Tt]he\s+parcel\s+parcel\s+ending\s+({suffix_pattern})\b", rf"The parcel ending \1"),
        (rf"\bparcel\s+parcel\s+ending\s+({suffix_pattern})\b", rf"parcel ending \1"),
        (rf"\b[Yy]our\s+tracking\s+number\s+tracking_number_ending_({suffix_pattern})\b", rf"Your parcel ending \1"),
        (rf"\btracking\s+number\s+tracking_number_ending_({suffix_pattern})\b", rf"parcel ending \1"),
        (rf"\btracking_number_ending_({suffix_pattern})\b", rf"parcel ending \1"),
        (rf"我提供的运单号\s*运单尾号\s*({suffix_pattern})", rf"您提供的运单尾号 \1"),
        (rf"我提供的运单号码\s*运单尾号\s*({suffix_pattern})", rf"您提供的运单尾号 \1"),
        (rf"我提供的单号\s*运单尾号\s*({suffix_pattern})", rf"您提供的运单尾号 \1"),
        (rf"您提供的运单号\s*运单尾号\s*({suffix_pattern})", rf"您提供的运单尾号 \1"),
        (rf"您提供的运单号码\s*运单尾号\s*({suffix_pattern})", rf"您提供的运单尾号 \1"),
        (rf"您的运单号\s*运单尾号\s*({suffix_pattern})", rf"您的运单尾号 \1"),
        (rf"您的运单号码\s*运单尾号\s*({suffix_pattern})", rf"您的运单尾号 \1"),
        (rf"运单号\s*运单尾号\s*({suffix_pattern})", rf"运单尾号 \1"),
        (rf"运单号码\s*运单尾号\s*({suffix_pattern})", rf"运单尾号 \1"),
    ]
    for pattern, replacement in replacements:
        polished = re.sub(pattern, replacement, polished, flags=re.IGNORECASE)
    polished = re.sub(r"是否完整且正确吗([？?])", r"是否完整且正确\1", polished)
    return " ".join(polished.split())


def _sanitize_reply_for_trusted_tracking(reply: Any, *, tracking_number: str | None, tracking_fact_metadata: dict[str, Any] | None) -> Any:
    if not isinstance(reply, str) or not _trusted_tracking_fact_present(tracking_fact_metadata):
        return reply
    raw = str(tracking_number or "").strip()
    if not raw:
        return reply
    suffix = _tracking_suffix(raw)
    if not suffix:
        return reply
    replacement = f"parcel ending {suffix}"
    cleaned = re.sub(re.escape(raw), replacement, reply, flags=re.IGNORECASE)
    return _polish_tracking_reference(cleaned, suffix=suffix)


def _sanitize_reply_for_tracking_reference(reply: Any, *, tracking_number: str | None) -> Any:
    if not isinstance(reply, str):
        return reply
    raw = str(tracking_number or "").strip()
    if not raw:
        return reply
    suffix = _tracking_suffix(raw)
    if not suffix:
        return reply
    replacement = f"运单尾号 {suffix}" if re.search(r"[\u4e00-\u9fff]", reply) else f"parcel ending {suffix}"
    cleaned = re.sub(re.escape(raw), replacement, reply, flags=re.IGNORECASE)
    compact_raw = "".join(ch for ch in raw.upper() if ch.isalnum())
    if compact_raw and compact_raw.upper() != raw.upper():
        cleaned = re.sub(re.escape(compact_raw), replacement, cleaned, flags=re.IGNORECASE)
    digits_raw = "".join(ch for ch in raw if ch.isdigit())
    if len(digits_raw) >= 8:
        cleaned = re.sub(re.escape(digits_raw), replacement, cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"(?<!\d)\d{8,}(?!\d)",
        lambda match: replacement if match.group(0).endswith(suffix) else match.group(0),
        cleaned,
    )
    cleaned = re.sub(
        r"(?<![A-Z0-9])(?:[A-Z]{1,4}[\s._-]*)?\d(?:[\s._-]*\d){7,}(?![A-Z0-9])",
        lambda match: replacement if "".join(ch for ch in match.group(0) if ch.isalnum()).upper().endswith(suffix) else match.group(0),
        cleaned,
        flags=re.IGNORECASE,
    )
    return _polish_tracking_reference(" ".join(cleaned.split()), suffix=suffix)


def _normalized_provider_tool_calls(value: Any, *, intent: str, tracking_number: str | None, tracking_fact_metadata: dict[str, Any] | None) -> list[dict[str, Any]]:
    tool_calls = list(value or []) if isinstance(value or [], list) else []
    trusted_tracking = _trusted_tracking_fact_present(tracking_fact_metadata)
    tracking_hash = _trusted_tracking_hash(metadata=tracking_fact_metadata, tracking_number=tracking_number)
    normalized: list[dict[str, Any]] = []
    has_speedaf_query = False
    for item in tool_calls[:12]:
        if not isinstance(item, dict):
            continue
        data = dict(item)
        tool_name = canonical_tool_name(data.get("tool_name") or data.get("name") or data.get("tool"))
        if not tool_name:
            continue
        data["tool_name"] = tool_name
        if tool_name == "speedaf.order.query":
            has_speedaf_query = True
            args = data.get("arguments") if isinstance(data.get("arguments"), dict) else {}
            args = dict(args)
            args.pop("tracking_number", None)
            args.pop("waybill", None)
            if tracking_hash:
                args["tracking_number_hash"] = tracking_hash
            data["arguments"] = args
            data["requires_confirmation"] = False
        normalized.append(data)
    if trusted_tracking and intent == "tracking" and tracking_number and not has_speedaf_query:
        normalized.extend(
            call.model_dump(exclude_none=True)
            for call in _default_tool_calls(
                intent=intent,
                handoff_required=False,
                tracking_number=tracking_number,
                tracking_fact_metadata=tracking_fact_metadata,
                handoff_reason=None,
            )
        )
    return normalized


def _tracking_fact_evidence(metadata: dict[str, Any] | None) -> AIDecisionEvidence | None:
    if not isinstance(metadata, dict):
        return None
    fact_present = _trusted_tracking_fact_present(metadata)
    source = "speedaf_trusted_tracking_fact" if fact_present else "speedaf_tracking_fact_unavailable"
    tracking_hash = metadata.get("tracking_number_hash")
    return AIDecisionEvidence(
        source=source,
        evidence_type="trusted_tracking_fact",
        evidence_id=str(metadata.get("tool_status") or metadata.get("tracking_fact_failure_reason") or source)[:240],
        fact_evidence_present=fact_present,
        tracking_number_hash=str(tracking_hash) if tracking_hash else None,
        raw_tracking_number_exposed=False,
    )


def _rag_evidence(runtime_context: dict[str, Any] | None) -> AIDecisionEvidence | None:
    if not isinstance(runtime_context, dict):
        return None
    knowledge = runtime_context.get("knowledge_context")
    if not isinstance(knowledge, dict):
        return None
    evidence_pack = [item for item in knowledge.get("evidence_pack") or [] if isinstance(item, dict)]
    hits = [item for item in knowledge.get("hits") or [] if isinstance(item, dict)]
    locked_facts = [item for item in knowledge.get("locked_facts") or [] if isinstance(item, dict)]
    accepted_evidence = evidence_pack or hits or locked_facts
    if not accepted_evidence:
        return None
    evidence_id = next(
        (
            str(item.get("item_key") or "").strip()
            for item in accepted_evidence
            if str(item.get("item_key") or "").strip()
        ),
        "hybrid_rag",
    )
    return AIDecisionEvidence(
        source="hybrid_rag",
        evidence_type="knowledge_context",
        evidence_id=evidence_id[:240],
        fact_evidence_present=True,
        raw_tracking_number_exposed=False,
    )


def _default_tool_calls(*, intent: str, handoff_required: bool, tracking_number: str | None, tracking_fact_metadata: dict[str, Any] | None, handoff_reason: str | None) -> list[AIDecisionToolCall]:
    calls: list[AIDecisionToolCall] = []
    if intent == "tracking" and tracking_number:
        calls.append(
            AIDecisionToolCall(
                tool_name="speedaf.order.query",
                arguments={"tracking_number_hash": hash_tracking_number(tracking_number)},
                reason="trusted_tracking_fact_required_for_live_status",
                requires_confirmation=False,
            )
        )
    if handoff_required:
        reason = _clip(handoff_reason, 240) or f"{intent}_requires_human_review"
        calls.append(
            AIDecisionToolCall(
                tool_name="handoff.request.create",
                arguments={"reason": reason, "intent": intent},
                reason="ai_requested_human_review_through_controlled_tool",
                requires_confirmation=False,
            )
        )
    return calls


def _decision_knowledge_context(runtime_context: dict[str, Any] | None) -> dict[str, Any]:
    knowledge = runtime_context.get("knowledge_context") if isinstance(runtime_context, dict) else None
    return knowledge if isinstance(knowledge, dict) else {}


def _decision_payload_from_provider(provider_result: Any, *, tracking_fact_metadata: dict[str, Any] | None = None, tracking_number: str | None = None, runtime_context: dict[str, Any] | None = None, request_body: str | None = None) -> dict[str, Any]:
    safe_summary = getattr(provider_result, "raw_payload_safe_summary", None) or {}
    raw_decision = safe_summary.get("ai_decision") if isinstance(safe_summary, dict) else None
    if isinstance(raw_decision, dict):
        payload = dict(raw_decision)
    else:
        payload = {}
    intent = normalize_intent(payload.get("intent") or getattr(provider_result, "intent", None))
    handoff_required = bool(payload.get("handoff_required", getattr(provider_result, "handoff_required", False)))
    provider_tracking = _safe_tracking_control_value(
        payload.get("tracking_number") or getattr(provider_result, "tracking_number", None),
        fallback_tracking_number=tracking_number,
    )
    reply = _sanitize_reply_for_tracking_reference(
        payload.get("customer_reply") or getattr(provider_result, "reply", None) or payload.get("reply"),
        tracking_number=provider_tracking,
    )
    reply = _sanitize_reply_for_trusted_tracking(
        reply,
        tracking_number=provider_tracking,
        tracking_fact_metadata=tracking_fact_metadata,
    )
    evidence_used: list[dict[str, Any]] = []
    tracking_evidence = _tracking_fact_evidence(tracking_fact_metadata)
    if tracking_evidence is not None:
        evidence_used.append(tracking_evidence.model_dump(exclude_none=True))
    rag_evidence = _rag_evidence(runtime_context)
    if rag_evidence is not None:
        evidence_used.append(rag_evidence.model_dump(exclude_none=True))
    tool_calls = _normalized_provider_tool_calls(
        payload.get("tool_calls"),
        intent=intent,
        tracking_number=provider_tracking,
        tracking_fact_metadata=tracking_fact_metadata,
    )
    if not tool_calls:
        tool_calls = [call.model_dump(exclude_none=True) for call in _default_tool_calls(intent=intent, handoff_required=handoff_required, tracking_number=provider_tracking, tracking_fact_metadata=tracking_fact_metadata, handoff_reason=payload.get("handoff_reason") or getattr(provider_result, "handoff_reason", None))]
    return {
        "customer_reply": reply,
        "intent": intent,
        "confidence": float(payload.get("confidence", 0.7 if getattr(provider_result, "ok", False) else 0.0) or 0.0),
        "risk_level": normalize_risk_level(payload.get("risk_level") or ("medium" if handoff_required else "low")),
        "next_action": normalize_next_action(payload.get("next_action"), handoff_required=handoff_required, has_tool_calls=bool(tool_calls), intent=intent),
        "handoff_required": handoff_required,
        "handoff_reason": _clip(payload.get("handoff_reason") or getattr(provider_result, "handoff_reason", None), 240),
        "tool_calls": tool_calls,
        "evidence_used": evidence_used,
        "safety_notes": payload.get("safety_notes") or [],
    }


def decision_from_provider_result(
    provider_result: Any,
    *,
    tracking_fact_metadata: dict[str, Any] | None = None,
    tracking_number: str | None = None,
    runtime_context: dict[str, Any] | None = None,
    request_body: str | None = None,
) -> AIDecision:
    try:
        decision = AIDecision.model_validate(
            _decision_payload_from_provider(
                provider_result,
                tracking_fact_metadata=tracking_fact_metadata,
                tracking_number=tracking_number,
                runtime_context=runtime_context,
                request_body=request_body,
            )
        )
        assert_customer_visible_reply_is_safe(
            decision.customer_reply,
            evidence_present=_trusted_tracking_fact_present(tracking_fact_metadata),
        )
        return decision
    except (ValidationError, RuntimeReplyParseError, ValueError) as exc:
        raise RuntimeReplyParseError(f"AI decision output is invalid: {exc}") from exc


def build_ai_decision_trace(
    *,
    decision: AIDecision,
    policy_result: PolicyGateResult,
    reply_source: str | None = None,
    mode: str = "gated",
    runtime_context: dict[str, Any] | None = None,
    tool_execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trace: dict[str, Any] = {
        "schema_version": AI_DECISION_SCHEMA_VERSION,
        "mode": mode,
        "reply_source": reply_source,
        "decision": decision.safe_public_summary(),
        "policy_gate": policy_result.safe_summary(),
        "tool_execution": tool_execution or {"ok": True, "records": []},
        "raw_tracking_number_exposed": False,
    }
    if runtime_context:
        trace["runtime_context_trace"] = summarize_rag_trace(runtime_context)
    return trace


def validate_and_trace_decision(
    *,
    decision: AIDecision,
    tracking_fact_metadata: dict[str, Any] | None = None,
    tracking_number: str | None = None,
    reply_source: str | None = None,
    runtime_context: dict[str, Any] | None = None,
    mode: str = "gated",
    request_id: str | None = None,
    tenant_key: str | None = None,
    channel_key: str | None = None,
    session_id: str | None = None,
) -> tuple[PolicyGateResult, dict[str, Any]]:
    auto_work_order_enabled = str(os.getenv("WEBCHAT_AI_AUTO_WORK_ORDER_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}
    policy = validate_ai_decision(
        decision,
        tracking_fact_metadata=tracking_fact_metadata,
        tracking_number=tracking_number,
        allow_high_risk_write_execution=auto_work_order_enabled,
        allowed_high_risk_write_tools={"speedaf.workOrder.create"} if auto_work_order_enabled else set(),
    )
    trace = build_ai_decision_trace(decision=decision, policy_result=policy, reply_source=reply_source, mode=mode, runtime_context=runtime_context)
    log_ai_decision_audit(
        event="webchat_ai_decision_validated",
        request_id=request_id,
        tenant_key=tenant_key,
        channel_key=channel_key,
        session_id=session_id,
        payload=trace,
    )
    return policy, trace
