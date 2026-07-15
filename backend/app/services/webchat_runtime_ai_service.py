from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from app.db import SessionLocal

from .ai_runtime.schemas import RuntimeAIProviderRequest, RuntimeAIProviderResult
from .ai_runtime_context import TRACKING_CONTEXT_RE, _looks_like_tracking_identifier, build_webchat_runtime_context
from .customer_language import detect_customer_language
from .domain_intelligence.webchat_shadow_bridge import build_webchat_domain_shadow_trace
from .knowledge_prompt_service import summarize_rag_trace
from .provider_runtime.output_contracts import OutputContracts
from .provider_runtime.webchat_runtime_dispatcher import dispatch_webchat_runtime_reply
from .webchat_ai_decision_runtime.service import decision_from_provider_result, validate_and_trace_decision
from .webchat_runtime_config import get_webchat_runtime_settings
from .webchat_runtime_output_parser import RuntimeReplyParseError, assert_customer_visible_reply_is_safe
from .webchat_runtime_metrics import record_webchat_runtime_metric


@dataclass(frozen=True)
class WebchatRuntimeReplyResult:
    ok: bool
    ai_generated: bool
    reply_source: str | None
    reply: str | None
    intent: str | None
    tracking_number: str | None
    handoff_required: bool
    handoff_reason: str | None
    recommended_agent_action: str | None
    ticket_creation_queued: bool
    elapsed_ms: int
    error_code: str | None = None
    retry_after_ms: int | None = None
    rag_trace: dict[str, Any] | None = None
    grounding_applied: bool = False
    grounding_source: dict[str, Any] | None = None
    grounding_reason: str | None = None
    ai_decision_trace: dict[str, Any] | None = None
    runtime_trace: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] | None = None

    def to_response(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("recommended_agent_action", None)
        rag_trace = payload.pop("rag_trace", None)
        if rag_trace:
            payload["evidence_trace"] = rag_trace
        return payload


def _clip(value: str | None, limit: int) -> str:
    cleaned = (value or "").strip()
    return cleaned[:limit]


def _polish_customer_visible_terms(reply: str | None) -> str | None:
    if not isinstance(reply, str):
        return reply
    cleaned = re.sub(r"waybill\s*号\s*运单尾号", "运单尾号", reply, flags=re.IGNORECASE)
    cleaned = re.sub(r"waybill\s*号码", "运单号", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"waybill\s*号", "运单号", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"waybill", "运单", cleaned, flags=re.IGNORECASE)
    return cleaned


def _clean_context(recent_context: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    settings = get_webchat_runtime_settings()
    items = recent_context or []
    cleaned: list[dict[str, str]] = []
    for item in items[-settings.history_turns * 2:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"customer", "visitor", "user", "ai", "assistant", "agent"}:
            continue
        normalized_role = "customer" if role in {"customer", "visitor", "user"} else "ai"
        text = _clip(str(item.get("text") or item.get("body") or ""), 500)
        if text:
            cleaned.append({"role": normalized_role, "text": text})
    return cleaned[-settings.history_turns * 2:]


def _result_from_provider(
    provider_result: RuntimeAIProviderResult,
    *,
    tracking_fact_metadata: dict[str, Any] | None = None,
    tracking_number: str | None = None,
    runtime_context: dict[str, Any] | None = None,
    tenant_key: str | None = None,
    channel_key: str | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
    body: str | None = None,
) -> WebchatRuntimeReplyResult:
    safe_summary = provider_result.raw_payload_safe_summary or {}
    grounded_reply_source = str(provider_result.reply_source or "").endswith(":grounded_knowledge")
    grounding_applied = bool(safe_summary.get("grounding_applied")) or grounded_reply_source
    ai_decision_trace = safe_summary.get("ai_decision_trace") if isinstance(safe_summary.get("ai_decision_trace"), dict) else None
    intent = provider_result.intent
    handoff_required = provider_result.handoff_required
    handoff_reason = provider_result.handoff_reason
    recommended_agent_action = provider_result.recommended_agent_action
    reply = _polish_customer_visible_terms(provider_result.reply)
    explicit_handoff_requested = _customer_explicitly_requests_handoff(body)
    accepted_tool_calls: list[dict[str, Any]] | None = None

    if provider_result.ok and _customer_visible_structure_leak(reply):
        blocked_summary = {
            **safe_summary,
            "error_code": "provider_reply_structure_leak",
        }
        return WebchatRuntimeReplyResult(
            ok=False,
            ai_generated=False,
            reply_source=provider_result.reply_source,
            reply=None,
            intent=provider_result.intent,
            tracking_number=None,
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            ticket_creation_queued=False,
            elapsed_ms=provider_result.elapsed_ms,
            error_code="ai_decision_invalid_output",
            retry_after_ms=1500,
            rag_trace=safe_summary.get("rag_trace"),
            grounding_applied=grounding_applied,
            grounding_source=safe_summary.get("grounding_source"),
            grounding_reason=safe_summary.get("grounding_reason"),
            ai_decision_trace=ai_decision_trace,
            runtime_trace=_runtime_trace_from_summary(blocked_summary),
        )

    if provider_result.ok and provider_result.reply:
        grounding_gate = _locked_fact_grounding_gate(
            provider_result.reply,
            runtime_context,
            body=body,
            tracking_fact_evidence_present=_trusted_tracking_fact_present(tracking_fact_metadata),
            parsed={
                "intent": provider_result.intent,
                "tracking_number": provider_result.tracking_number,
                "handoff_required": provider_result.handoff_required,
            },
        )
        if grounding_gate.get("status") == "fail":
            blocked_summary = {
                **safe_summary,
                "grounding_validation": "fail",
                "grounding_violation": grounding_gate.get("reason") or "reply_not_fact_equivalent",
                "error_code": "locked_fact_grounding_conflict",
            }
            return WebchatRuntimeReplyResult(
                ok=False,
                ai_generated=False,
                reply_source=provider_result.reply_source,
                reply=None,
                intent=provider_result.intent,
                tracking_number=None,
                handoff_required=False,
                handoff_reason=None,
                recommended_agent_action=None,
                ticket_creation_queued=False,
                elapsed_ms=provider_result.elapsed_ms,
                error_code="locked_fact_grounding_conflict",
                retry_after_ms=1500,
                rag_trace=blocked_summary.get("rag_trace"),
                grounding_applied=False,
                grounding_source=blocked_summary.get("grounding_source"),
                grounding_reason=blocked_summary.get("grounding_reason"),
                ai_decision_trace=ai_decision_trace,
                runtime_trace=_runtime_trace_from_summary(blocked_summary),
            )
        if grounding_gate.get("status") == "pass":
            safe_summary = {
                **safe_summary,
                "grounding_validation": "pass",
                "grounding_applied": True,
                "grounding_source": grounding_gate.get("source"),
                "grounding_reason": "locked_fact_ai_grounded",
            }

    if provider_result.ok and provider_result.reply:
        try:
            decision = decision_from_provider_result(
                provider_result,
                tracking_fact_metadata=tracking_fact_metadata,
                tracking_number=tracking_number or provider_result.tracking_number,
                runtime_context=runtime_context,
                request_body=body,
            )
            policy, ai_decision_trace = validate_and_trace_decision(
                decision=decision,
                tracking_fact_metadata=tracking_fact_metadata,
                tracking_number=tracking_number or provider_result.tracking_number,
                reply_source=provider_result.reply_source,
                runtime_context=runtime_context,
                mode="gated",
                request_id=request_id,
                tenant_key=tenant_key,
                channel_key=channel_key,
                session_id=session_id,
            )
            safe_summary = {
                **safe_summary,
                **_ai_decision_trace_runtime_fields(ai_decision_trace),
            }
            if not policy.ok:
                negative_tracking_reply = _safe_negative_tracking_lookup_reply(
                    decision.customer_reply,
                    tracking_fact_metadata=tracking_fact_metadata,
                )
                if negative_tracking_reply:
                    handoff_override = _explicit_handoff_control_override(
                        explicit_handoff_requested=explicit_handoff_requested,
                        handoff_required=False,
                        handoff_reason=None,
                        recommended_agent_action=None,
                    )
                    if handoff_override["applied"]:
                        safe_summary = {
                            **safe_summary,
                            "ai_decision_control_override_reason": "explicit_customer_handoff_request",
                        }
                    safe_summary = {
                        **safe_summary,
                        "ai_decision_soft_accept_reason": "negative_tracking_lookup_policy_allow",
                        "ai_decision_policy_ok": True,
                    }
                    safe_summary.pop("ai_decision_policy_violation_codes", None)
                    return WebchatRuntimeReplyResult(
                        ok=True,
                        ai_generated=True,
                        reply_source=provider_result.reply_source,
                        reply=_polish_customer_visible_terms(negative_tracking_reply),
                        intent=decision.intent,
                        tracking_number=None,
                        handoff_required=handoff_override["handoff_required"],
                        handoff_reason=handoff_override["handoff_reason"],
                        recommended_agent_action=handoff_override["recommended_agent_action"],
                        ticket_creation_queued=False,
                        elapsed_ms=provider_result.elapsed_ms,
                        error_code=None,
                        retry_after_ms=provider_result.retry_after_ms,
                        rag_trace=safe_summary.get("rag_trace"),
                        grounding_applied=grounding_applied,
                        grounding_source=safe_summary.get("grounding_source"),
                        grounding_reason=safe_summary.get("grounding_reason"),
                        ai_decision_trace=ai_decision_trace,
                        runtime_trace=_runtime_trace_from_summary(safe_summary),
                    )
                blocked_summary = {
                    **safe_summary,
                    "error_code": "ai_decision_policy_blocked",
                    **_ai_decision_trace_runtime_fields(ai_decision_trace),
                }
                return WebchatRuntimeReplyResult(
                    ok=False,
                    ai_generated=False,
                    reply_source=provider_result.reply_source,
                    reply=None,
                    intent=decision.intent,
                    tracking_number=None,
                    handoff_required=False,
                    handoff_reason=None,
                    recommended_agent_action=None,
                    ticket_creation_queued=False,
                    elapsed_ms=provider_result.elapsed_ms,
                    error_code="ai_decision_policy_blocked",
                    retry_after_ms=1500,
                    rag_trace=safe_summary.get("rag_trace"),
                    grounding_applied=grounding_applied,
                    grounding_source=safe_summary.get("grounding_source"),
                    grounding_reason=safe_summary.get("grounding_reason"),
                    ai_decision_trace=ai_decision_trace,
                    runtime_trace=_runtime_trace_from_summary(blocked_summary),
                )
            guard = _trusted_tracking_handoff_guard(
                reply=decision.customer_reply,
                tracking_fact_metadata=tracking_fact_metadata,
                explicit_handoff_requested=explicit_handoff_requested,
                handoff_required=decision.handoff_required,
                handoff_reason=decision.handoff_reason,
                recommended_agent_action=recommended_agent_action,
            )
            if guard["applied"]:
                safe_summary = {
                    **safe_summary,
                    "ai_decision_control_override_reason": "trusted_tracking_no_auto_handoff",
                }
                decision = decision.model_copy(
                    update={
                        "customer_reply": guard["reply"],
                        "handoff_required": False,
                        "handoff_reason": None,
                        "next_action": "reply",
                    }
                )
            intent = decision.intent
            handoff_required = decision.handoff_required
            handoff_reason = decision.handoff_reason
            accepted_tool_calls = [call.model_dump(exclude_none=True) for call in decision.tool_calls]
            recommended_agent_action = provider_result.recommended_agent_action
            if guard["applied"]:
                recommended_agent_action = None
            if _negative_tracking_lookup_present(tracking_fact_metadata) and not _customer_explicitly_requests_handoff(body):
                if handoff_required or handoff_reason or recommended_agent_action:
                    safe_summary = {
                        **safe_summary,
                        "ai_decision_control_override_reason": "negative_tracking_lookup_no_auto_handoff",
                    }
                handoff_required = False
                handoff_reason = None
                recommended_agent_action = None
            handoff_override = _explicit_handoff_control_override(
                explicit_handoff_requested=explicit_handoff_requested,
                handoff_required=handoff_required,
                handoff_reason=handoff_reason,
                recommended_agent_action=recommended_agent_action,
            )
            if handoff_override["applied"]:
                safe_summary = {
                    **safe_summary,
                    "ai_decision_control_override_reason": "explicit_customer_handoff_request",
                }
                handoff_required = handoff_override["handoff_required"]
                handoff_reason = handoff_override["handoff_reason"]
                recommended_agent_action = handoff_override["recommended_agent_action"]
            reply = _polish_customer_visible_terms(decision.customer_reply)
        except RuntimeReplyParseError:
            soft_reply = _trusted_tracking_soft_accept_reply(provider_result.reply, tracking_fact_metadata=tracking_fact_metadata)
            if provider_result.ok and soft_reply:
                guard = _trusted_tracking_handoff_guard(
                    reply=soft_reply,
                    tracking_fact_metadata=tracking_fact_metadata,
                    explicit_handoff_requested=explicit_handoff_requested,
                    handoff_required=provider_result.handoff_required,
                    handoff_reason=provider_result.handoff_reason,
                    recommended_agent_action=provider_result.recommended_agent_action,
                )
                if guard["applied"]:
                    safe_summary = {
                        **safe_summary,
                        "ai_decision_control_override_reason": "trusted_tracking_no_auto_handoff",
                    }
                    soft_reply = guard["reply"]
                handoff_override = _explicit_handoff_control_override(
                    explicit_handoff_requested=explicit_handoff_requested,
                    handoff_required=guard["handoff_required"],
                    handoff_reason=guard["handoff_reason"],
                    recommended_agent_action=guard["recommended_agent_action"],
                )
                if handoff_override["applied"]:
                    safe_summary = {
                        **safe_summary,
                        "ai_decision_control_override_reason": "explicit_customer_handoff_request",
                    }
                safe_summary = {
                    **safe_summary,
                    "ai_decision_soft_accept_reason": "trusted_tracking_provider_reply",
                }
                return WebchatRuntimeReplyResult(
                    ok=True,
                    ai_generated=True,
                    reply_source=provider_result.reply_source,
                    reply=_polish_customer_visible_terms(soft_reply),
                    intent=provider_result.intent or "tracking",
                    tracking_number=provider_result.tracking_number,
                    handoff_required=handoff_override["handoff_required"],
                    handoff_reason=handoff_override["handoff_reason"],
                    recommended_agent_action=handoff_override["recommended_agent_action"],
                    ticket_creation_queued=False,
                    elapsed_ms=provider_result.elapsed_ms,
                    error_code=None,
                    retry_after_ms=provider_result.retry_after_ms,
                    rag_trace=safe_summary.get("rag_trace"),
                    grounding_applied=grounding_applied,
                    grounding_source=safe_summary.get("grounding_source"),
                    grounding_reason=safe_summary.get("grounding_reason"),
                    ai_decision_trace=ai_decision_trace,
                    runtime_trace=_runtime_trace_from_summary(safe_summary),
                )
            soft_reply = _safe_provider_reply_soft_accept_reply(provider_result.reply, tracking_fact_metadata=tracking_fact_metadata)
            if provider_result.ok and soft_reply:
                handoff_override = _explicit_handoff_control_override(
                    explicit_handoff_requested=explicit_handoff_requested,
                    handoff_required=False,
                    handoff_reason=None,
                    recommended_agent_action=None,
                )
                if handoff_override["applied"]:
                    safe_summary = {
                        **safe_summary,
                        "ai_decision_control_override_reason": "explicit_customer_handoff_request",
                    }
                safe_summary = {
                    **safe_summary,
                    "ai_decision_soft_accept_reason": "provider_reply_safe_decision_parse_failed",
                }
                return WebchatRuntimeReplyResult(
                    ok=True,
                    ai_generated=True,
                    reply_source=provider_result.reply_source,
                    reply=_polish_customer_visible_terms(soft_reply),
                    intent=provider_result.intent or "other",
                    tracking_number=provider_result.tracking_number,
                    handoff_required=handoff_override["handoff_required"],
                    handoff_reason=handoff_override["handoff_reason"],
                    recommended_agent_action=handoff_override["recommended_agent_action"],
                    ticket_creation_queued=False,
                    elapsed_ms=provider_result.elapsed_ms,
                    error_code=None,
                    retry_after_ms=provider_result.retry_after_ms,
                    rag_trace=safe_summary.get("rag_trace"),
                    grounding_applied=grounding_applied,
                    grounding_source=safe_summary.get("grounding_source"),
                    grounding_reason=safe_summary.get("grounding_reason"),
                    ai_decision_trace=ai_decision_trace,
                    runtime_trace=_runtime_trace_from_summary(safe_summary),
                )
            return WebchatRuntimeReplyResult(
                ok=False,
                ai_generated=False,
                reply_source=provider_result.reply_source,
                reply=None,
                intent=provider_result.intent,
                tracking_number=None,
                handoff_required=False,
                handoff_reason=None,
                recommended_agent_action=None,
                ticket_creation_queued=False,
                elapsed_ms=provider_result.elapsed_ms,
                error_code="ai_decision_invalid_output",
                retry_after_ms=1500,
                rag_trace=safe_summary.get("rag_trace"),
                grounding_applied=grounding_applied,
                grounding_source=safe_summary.get("grounding_source"),
                grounding_reason=safe_summary.get("grounding_reason"),
                ai_decision_trace=ai_decision_trace,
                runtime_trace=_runtime_trace_from_summary({**safe_summary, "error_code": "ai_decision_invalid_output"}),
            )

    handoff_override = _explicit_handoff_control_override(
        explicit_handoff_requested=explicit_handoff_requested,
        handoff_required=handoff_required,
        handoff_reason=handoff_reason,
        recommended_agent_action=recommended_agent_action,
    )
    if handoff_override["applied"]:
        safe_summary = {
            **safe_summary,
            "ai_decision_control_override_reason": "explicit_customer_handoff_request",
        }
        handoff_required = handoff_override["handoff_required"]
        handoff_reason = handoff_override["handoff_reason"]
        recommended_agent_action = handoff_override["recommended_agent_action"]

    return WebchatRuntimeReplyResult(
        ok=provider_result.ok,
        ai_generated=provider_result.ai_generated,
        reply_source=provider_result.reply_source,
        reply=reply,
        intent=intent,
        tracking_number=provider_result.tracking_number,
        handoff_required=handoff_required,
        handoff_reason=handoff_reason,
        recommended_agent_action=recommended_agent_action,
        ticket_creation_queued=False,
        elapsed_ms=provider_result.elapsed_ms,
        error_code=provider_result.error_code,
        retry_after_ms=provider_result.retry_after_ms,
        rag_trace=safe_summary.get("rag_trace"),
        grounding_applied=grounding_applied,
        grounding_source=safe_summary.get("grounding_source"),
        grounding_reason=safe_summary.get("grounding_reason"),
        ai_decision_trace=ai_decision_trace,
        runtime_trace=_runtime_trace_from_summary(safe_summary),
        tool_calls=accepted_tool_calls,
    )


def _runtime_trace_from_summary(safe_summary: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "latency_class",
        "prompt_profile",
        "prompt_chars",
        "output_contract_repair_applied",
        "output_contract_repair_reason",
        "output_contract_soft_accept_reason",
        "ai_decision_soft_accept_reason",
        "ai_decision_control_override_reason",
        "ai_decision_policy_ok",
        "ai_decision_policy_violation_codes",
        "ai_decision_policy_warning_count",
        "ai_decision_checked_tools",
        "ai_decision_intent",
        "ai_decision_next_action",
        "ai_decision_handoff_required",
        "ai_decision_confidence",
        "grounding_validation",
        "grounding_violation",
        "error_code",
        "elapsed_ms",
        "timeout_seconds",
        "model",
        "model_policy",
        "model_reason",
        "chat_mode",
        "request_shape",
        "runtime_usage",
        "ollama_keep_alive",
        "max_contract_repair_attempts",
    )
    trace = {key: safe_summary.get(key) for key in keys if safe_summary.get(key) is not None}
    used_sources = safe_summary.get("ai_decision_used_sources")
    if isinstance(used_sources, list):
        trace["ai_decision_used_sources"] = [
            str(item)[:240]
            for item in used_sources[:20]
            if isinstance(item, str) and item.strip()
        ]
    ollama_options = safe_summary.get("ollama_options")
    if isinstance(ollama_options, dict) and isinstance(ollama_options.get("num_predict"), (int, float)):
        trace["ollama_num_predict"] = ollama_options["num_predict"]
    if isinstance(ollama_options, dict) and isinstance(ollama_options.get("num_ctx"), (int, float)):
        trace["ollama_num_ctx"] = ollama_options["num_ctx"]
    return trace


def _ai_decision_trace_runtime_fields(ai_decision_trace: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(ai_decision_trace, dict):
        return {}
    policy = ai_decision_trace.get("policy_gate") if isinstance(ai_decision_trace.get("policy_gate"), dict) else {}
    decision = ai_decision_trace.get("decision") if isinstance(ai_decision_trace.get("decision"), dict) else {}
    violations = policy.get("violations") if isinstance(policy.get("violations"), list) else []
    checked_tools = policy.get("checked_tools") if isinstance(policy.get("checked_tools"), list) else []
    warnings = policy.get("warnings") if isinstance(policy.get("warnings"), list) else []
    codes = sorted(
        {
            str(item.get("code") or "").strip()
            for item in violations
            if isinstance(item, dict) and str(item.get("code") or "").strip()
        }
    )
    fields: dict[str, Any] = {}
    if "ok" in policy:
        fields["ai_decision_policy_ok"] = bool(policy.get("ok"))
    if codes:
        fields["ai_decision_policy_violation_codes"] = ",".join(codes[:8])
    if warnings:
        fields["ai_decision_policy_warning_count"] = len(warnings)
    clean_tools = [str(item)[:80] for item in checked_tools if isinstance(item, str) and item.strip()]
    if clean_tools:
        fields["ai_decision_checked_tools"] = ",".join(clean_tools[:8])
    for source_key, trace_key in (
        ("intent", "ai_decision_intent"),
        ("next_action", "ai_decision_next_action"),
        ("handoff_required", "ai_decision_handoff_required"),
    ):
        value = decision.get(source_key)
        if isinstance(value, (bool, int, float, str)):
            fields[trace_key] = value
    confidence = decision.get("confidence")
    if isinstance(confidence, (int, float)):
        fields["ai_decision_confidence"] = max(0.0, min(1.0, float(confidence)))
    evidence_used = decision.get("evidence_used") if isinstance(decision.get("evidence_used"), list) else []
    used_sources: list[str] = []
    for item in evidence_used[:20]:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        evidence_id = str(item.get("evidence_id") or "").strip()
        if source:
            used_sources.append(f"{source}:{evidence_id}"[:240] if evidence_id else source[:240])
    if used_sources:
        fields["ai_decision_used_sources"] = used_sources
    return fields


def _locked_fact_grounding_gate(
    reply: str | None,
    runtime_context: dict[str, Any] | None,
    *,
    body: str | None = None,
    tracking_fact_evidence_present: bool = False,
    parsed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(runtime_context, dict):
        return {"status": "not_applicable", "locked_fact_ids": []}
    knowledge = runtime_context.get("knowledge_context")
    if not isinstance(knowledge, dict):
        return {"status": "not_applicable", "locked_fact_ids": []}
    if OutputContracts._trusted_tracking_reply_can_bypass_locked_facts(
        evidence_present=tracking_fact_evidence_present,
        request_body=body,
        parsed=parsed,
    ):
        return {
            "status": "skipped",
            "locked_fact_ids": [],
            "reason": "trusted_tracking_fact_reply",
        }
    validation_context = knowledge
    try:
        from .provider_runtime.adapters.private_ai_runtime import _customer_intent_hint, _customer_visible_knowledge_context

        intent_hint = _customer_intent_hint(body)
        compact_context = _customer_visible_knowledge_context(
            knowledge,
            direct_answer_only=intent_hint == "service_or_policy",
            derive_locked_facts=intent_hint == "service_or_policy",
        )
        if compact_context.get("locked_facts"):
            validation_context = compact_context
    except Exception:
        validation_context = knowledge
    return OutputContracts.locked_fact_validation(reply, validation_context)


def _trusted_tracking_fact_present(metadata: dict[str, Any] | None) -> bool:
    if not isinstance(metadata, dict) or not metadata.get("pii_redacted"):
        return False
    return bool(metadata.get("fact_evidence_present") or metadata.get("tool_status") == "success")


def _trusted_tracking_soft_accept_reply(reply: str | None, *, tracking_fact_metadata: dict[str, Any] | None) -> str | None:
    if not _trusted_tracking_fact_present(tracking_fact_metadata):
        return None
    if not isinstance(reply, str):
        return None
    cleaned = " ".join(reply.strip().split())
    if not cleaned:
        return None
    if _customer_visible_structure_leak(cleaned):
        return None
    try:
        assert_customer_visible_reply_is_safe(cleaned, evidence_present=True)
    except Exception:
        return None
    return cleaned


def _safe_provider_reply_soft_accept_reply(reply: str | None, *, tracking_fact_metadata: dict[str, Any] | None) -> str | None:
    if not isinstance(reply, str):
        return None
    cleaned = " ".join(reply.strip().split())
    if not cleaned:
        return None
    if _customer_visible_structure_leak(cleaned):
        return None
    try:
        assert_customer_visible_reply_is_safe(cleaned, evidence_present=_trusted_tracking_fact_present(tracking_fact_metadata))
    except Exception:
        return None
    return cleaned


def _customer_visible_structure_leak(reply: str | None) -> bool:
    if not isinstance(reply, str):
        return False
    cleaned = reply.strip()
    if not cleaned:
        return False
    lowered_head = cleaned[:320].lower()
    return (
        cleaned.startswith("{")
        or cleaned.startswith("[")
        or "customer_reply" in lowered_head
        or '"reply"' in lowered_head
        or "handoff_required" in lowered_head
        or "ticket_should_create" in lowered_head
    )


_UNIFIED_RUNTIME_LATENCY_CLASS = "unified_ai_runtime"
_UNIFIED_RUNTIME_PROMPT_PROFILE = "unified_ai_runtime"
_TRUSTED_TRACKING_LATENCY_CLASS = "trusted_tracking_fact"
_TRUSTED_TRACKING_PROMPT_PROFILE = "trusted_tracking_fact"


def _latency_class_for_request(*, body: str | None, evidence_present: bool) -> str:
    if evidence_present:
        return _TRUSTED_TRACKING_LATENCY_CLASS
    return _UNIFIED_RUNTIME_LATENCY_CLASS


def _latency_class_with_runtime_context(
    latency_class: str,
    *,
    body: str | None,
    runtime_context: dict[str, Any] | None,
    evidence_present: bool,
) -> str:
    if evidence_present:
        return _TRUSTED_TRACKING_LATENCY_CLASS
    return _UNIFIED_RUNTIME_LATENCY_CLASS


def _runtime_context_with_latency_profile(runtime_context: dict[str, Any] | None, *, latency_class: str) -> dict[str, Any]:
    if not isinstance(runtime_context, dict):
        runtime_context = {}
    profiled = dict(runtime_context)
    profiled.setdefault("context_version", "nexus.webchat_runtime_context")
    if latency_class == _TRUSTED_TRACKING_LATENCY_CLASS:
        profiled["latency_class"] = _TRUSTED_TRACKING_LATENCY_CLASS
        profiled["runtime_prompt_profile"] = _TRUSTED_TRACKING_PROMPT_PROFILE
    else:
        profiled["latency_class"] = _UNIFIED_RUNTIME_LATENCY_CLASS
        profiled["runtime_prompt_profile"] = _UNIFIED_RUNTIME_PROMPT_PROFILE
    return profiled


def _runtime_context_with_language_policy(
    runtime_context: dict[str, Any] | None,
    *,
    language: str | None,
    source: str,
    confidence: float,
) -> dict[str, Any]:
    if not isinstance(runtime_context, dict):
        runtime_context = {}
    profiled = dict(runtime_context)
    metadata_filters = dict(profiled.get("metadata_filters") or {})
    metadata_filters["language"] = language
    profiled["metadata_filters"] = metadata_filters
    profiled["language"] = language
    profiled["customer_language"] = language
    profiled["customer_language_source"] = source
    profiled["customer_language_confidence"] = round(float(confidence or 0.0), 3)
    profiled["reply_language_policy"] = "same_as_latest_customer_message"
    return profiled


def _trusted_tracking_runtime_context() -> dict[str, Any]:
    return {
        "context_version": "nexus.webchat_runtime_context",
        "knowledge_context": {},
        "retrieval": "trusted_tracking_fact_only",
        "candidate_count": 0,
        "total_matches": 0,
        "retrieval_methods": ["skipped_for_trusted_tracking_fact"],
        "no_answer_reason": "trusted_tracking_fact_authoritative",
        "latency_ms": 0,
        "top_hits": [],
        "evidence_pack": [],
        "injected_knowledge": [],
        "grounding_would_apply": False,
        "grounding_source": None,
    }


def _runtime_context_for_request(
    *,
    tenant_key: str,
    channel_key: str,
    body: str,
    market_id: int | None,
    language: str | None,
    tracking_number: str | None = None,
    tracking_fact_evidence_present: bool | None = None,
) -> dict[str, Any] | None:
    db = SessionLocal()
    try:
        runtime_context = build_webchat_runtime_context(
            db,
            tenant_key=tenant_key,
            channel_key=channel_key,
            body=body,
            market_id=market_id,
            language=language,
            tracking_number=tracking_number,
            tracking_fact_evidence_present=tracking_fact_evidence_present,
        )
        return _attach_domain_shadow_trace(
            runtime_context,
            body=body,
            tenant_key=tenant_key,
            channel_key=channel_key,
            market_id=market_id,
            language=language,
        )
    except Exception:
        return None
    finally:
        db.close()


def _attach_domain_shadow_trace(
    runtime_context: dict[str, Any] | None,
    *,
    body: str,
    tenant_key: str,
    channel_key: str,
    market_id: int | None,
    language: str | None,
) -> dict[str, Any] | None:
    if not isinstance(runtime_context, dict):
        return runtime_context
    try:
        trace = build_webchat_domain_shadow_trace(
            body=body,
            tenant_key=tenant_key,
            channel_key=channel_key,
            market_id=market_id,
            language=language,
        )
    except Exception:
        trace = None
    if not trace:
        return runtime_context
    return {**runtime_context, "domain_intelligence_trace": trace}


def _provider_result_with_summary(provider_result: RuntimeAIProviderResult, safe_summary: dict[str, Any]) -> RuntimeAIProviderResult:
    return RuntimeAIProviderResult(**{**provider_result.__dict__, "raw_payload_safe_summary": safe_summary})


_REPAIRABLE_PRIVACY_POLICY_CODES = {"raw_tracking_exposed", "raw_caller_or_secret_exposed"}
_TRACKING_LIKE_TOKEN_RE = re.compile(r"\b(?=[A-Z0-9._-]{8,48}\b)(?=[A-Z0-9._-]*\d)[A-Z0-9][A-Z0-9._-]+\b", re.I)
_LONG_NUMERIC_RE = re.compile(r"(?<!\d)\d{8,}(?!\d)")


def _policy_violation_codes(result: WebchatRuntimeReplyResult) -> set[str]:
    trace = result.ai_decision_trace if isinstance(result.ai_decision_trace, dict) else {}
    policy = trace.get("policy_gate") if isinstance(trace.get("policy_gate"), dict) else {}
    violations = policy.get("violations") if isinstance(policy.get("violations"), list) else []
    return {
        str(item.get("code") or "").strip()
        for item in violations
        if isinstance(item, dict) and str(item.get("code") or "").strip()
    }


def _policy_blocked_only_by_repairable_privacy(result: WebchatRuntimeReplyResult) -> bool:
    if result.ok or result.error_code != "ai_decision_policy_blocked":
        return False
    codes = _policy_violation_codes(result)
    return bool(codes) and codes.issubset(_REPAIRABLE_PRIVACY_POLICY_CODES)


_NEGATIVE_TRACKING_MARKERS = (
    "未查到",
    "查不到",
    "没有查到",
    "无法找到",
    "找不到",
    "无验证结果",
    "没有验证结果",
    "尚未查到",
    "not found",
    "cannot find",
    "could not find",
    "unable to find",
    "no verified result",
    "no verified tracking",
    "确认号码是否完整",
    "号码是否完整",
    "号码是否正确",
    "完整正确",
    "完整且正确",
    "核对号码",
    "检查号码",
    "check whether the number",
    "check if the number",
    "number is complete",
    "number is correct",
    "complete and correct",
)


def _negative_tracking_lookup_present(metadata: dict[str, Any] | None) -> bool:
    if not isinstance(metadata, dict) or not metadata.get("pii_redacted"):
        return False
    status = str(metadata.get("tool_status") or "").strip().lower()
    if status in {"error", "failed", "not_found", "no_result", "unavailable"}:
        return True
    return bool(metadata.get("tracking_fact_failure_reason") or metadata.get("error_code"))


def _customer_explicitly_requests_handoff(body: str | None) -> bool:
    text = str(body or "").strip().lower()
    if not text:
        return False
    markers = (
        "human",
        "agent",
        "representative",
        "real person",
        "live person",
        "talk to someone",
        "speak to someone",
        "人工",
        "真人",
        "转人工",
        "人工客服",
        "客服接管",
        "人工处理",
    )
    return any(marker in text for marker in markers)


_HANDOFF_PROMISE_RE = re.compile(
    r"("
    r"\b(?:i|we)\s*(?:will|'ll|can|am going to|are going to)?\s*(?:now\s*)?"
    r"(?:route|connect|transfer|escalate|forward|pass)\b[^.!?。！？]{0,180}\b(?:human|agent|representative|support team|team)\b"
    r"|"
    r"\b(?:this|the case|your case|it)\s*(?:will|can|is going to)\s*"
    r"(?:be\s*)?(?:routed|connected|transferred|escalated|forwarded|passed)\b[^.!?。！？]{0,180}\b(?:human|agent|representative|support team|team)\b"
    r"|"
    r"\b(?:a|an|our)?\s*(?:human|support)?\s*agent\b[^.!?。！？]{0,160}\b(?:will|can)\s*(?:assist|help|take over|contact)\b"
    r")",
    re.IGNORECASE,
)

_CHINESE_HANDOFF_PROMISE_RE = re.compile(
    r"(?:转人工|转接人工|升级人工|人工客服(?:会|将|可以)?(?:接管|处理|协助)|(?:会|将)由人工(?:客服)?(?:接管|处理|协助))"
)


def _trusted_tracking_fact_needs_human_review(metadata: dict[str, Any] | None) -> bool:
    if not _trusted_tracking_fact_present(metadata):
        return False
    status_context = metadata.get("status_context") if isinstance(metadata, dict) and isinstance(metadata.get("status_context"), dict) else {}
    if status_context.get("needs_human_review") is True:
        return True
    lifecycle = metadata.get("tracking_lifecycle") if isinstance(metadata, dict) and isinstance(metadata.get("tracking_lifecycle"), dict) else {}
    risk = lifecycle.get("risk") if isinstance(lifecycle.get("risk"), dict) else {}
    return risk.get("escalate_required") is True


def _contains_handoff_promise(reply: str | None) -> bool:
    if not isinstance(reply, str) or not reply.strip():
        return False
    return bool(_HANDOFF_PROMISE_RE.search(reply) or _CHINESE_HANDOFF_PROMISE_RE.search(reply))


def _strip_unauthorized_handoff_promise(reply: str | None) -> str | None:
    if not isinstance(reply, str):
        return reply
    text = " ".join(reply.strip().split())
    if not text:
        return text
    parts = re.split(r"(?<=[.!?。！？])\s+", text)
    kept: list[str] = []
    for part in parts:
        if _contains_handoff_promise(part):
            continue
        kept.append(part)
    stripped = " ".join(part.strip() for part in kept if part.strip())
    if stripped:
        return stripped
    stripped = _HANDOFF_PROMISE_RE.sub("", text)
    stripped = _CHINESE_HANDOFF_PROMISE_RE.sub("", stripped)
    return " ".join(stripped.strip(" .。!！?？").split())


def _trusted_tracking_handoff_guard(
    *,
    reply: str | None,
    tracking_fact_metadata: dict[str, Any] | None,
    explicit_handoff_requested: bool,
    handoff_required: bool,
    handoff_reason: str | None,
    recommended_agent_action: str | None,
) -> dict[str, Any]:
    if (
        explicit_handoff_requested
        or not _trusted_tracking_fact_present(tracking_fact_metadata)
        or _trusted_tracking_fact_needs_human_review(tracking_fact_metadata)
    ):
        return {
            "applied": False,
            "reply": reply,
            "handoff_required": bool(handoff_required),
            "handoff_reason": handoff_reason,
            "recommended_agent_action": recommended_agent_action,
        }
    needs_guard = bool(handoff_required or handoff_reason or recommended_agent_action or _contains_handoff_promise(reply))
    if not needs_guard:
        return {
            "applied": False,
            "reply": reply,
            "handoff_required": False,
            "handoff_reason": None,
            "recommended_agent_action": None,
        }
    stripped = _strip_unauthorized_handoff_promise(reply)
    return {
        "applied": True,
        "reply": stripped or reply,
        "handoff_required": False,
        "handoff_reason": None,
        "recommended_agent_action": None,
    }


def _explicit_handoff_control_override(
    *,
    explicit_handoff_requested: bool,
    handoff_required: bool,
    handoff_reason: str | None,
    recommended_agent_action: str | None,
) -> dict[str, Any]:
    if not explicit_handoff_requested or handoff_required:
        return {
            "applied": False,
            "handoff_required": bool(handoff_required),
            "handoff_reason": handoff_reason,
            "recommended_agent_action": recommended_agent_action,
        }
    return {
        "applied": True,
        "handoff_required": True,
        "handoff_reason": "customer_requested_human_review",
        "recommended_agent_action": "Customer explicitly requested a human agent. Review the conversation and take over.",
    }


def _tracking_metadata_for_policy(*, metadata: dict[str, Any] | None, evidence_present: bool) -> dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return None
    if evidence_present:
        return metadata
    if _negative_tracking_lookup_present(metadata):
        return metadata
    return None


def _safe_negative_tracking_lookup_reply(reply: str | None, *, tracking_fact_metadata: dict[str, Any] | None) -> str | None:
    if not _negative_tracking_lookup_present(tracking_fact_metadata):
        return None
    cleaned = " ".join(str(reply or "").strip().split())
    if not cleaned:
        return None
    lowered = cleaned.lower()
    if not any(marker in lowered for marker in _NEGATIVE_TRACKING_MARKERS):
        return None
    if _customer_visible_structure_leak(cleaned):
        return None
    if _TRACKING_LIKE_TOKEN_RE.search(cleaned) or _LONG_NUMERIC_RE.search(cleaned):
        return None
    try:
        assert_customer_visible_reply_is_safe(cleaned, evidence_present=False)
    except Exception:
        return None
    return cleaned


def _tracking_reference_candidates(*, body: str | None, tracking_number: str | None, reply: str | None) -> list[str]:
    candidates: list[str] = []
    for value in (tracking_number, body, reply):
        text = str(value or "")
        if not text:
            continue
        if value == tracking_number and text.strip():
            candidates.append(text.strip())
        candidates.extend(match.group(0) for match in _TRACKING_LIKE_TOKEN_RE.finditer(text))
        candidates.extend(match.group(0) for match in _LONG_NUMERIC_RE.finditer(text))
    seen: set[str] = set()
    ordered: list[str] = []
    for item in candidates:
        cleaned = item.strip()
        key = re.sub(r"[^A-Z0-9]", "", cleaned.upper())
        if len(key) < 8 or key in seen:
            continue
        seen.add(key)
        ordered.append(cleaned)
    return ordered


def _identifier_suffix(value: str | None) -> str | None:
    cleaned = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    return cleaned[-6:] if len(cleaned) >= 6 else None


def _redact_reply_for_repair_context(reply: str | None, *, body: str | None, tracking_number: str | None) -> str | None:
    cleaned = str(reply or "").strip()
    if not cleaned:
        return None
    for candidate in _tracking_reference_candidates(body=body, tracking_number=tracking_number, reply=reply):
        suffix = _identifier_suffix(candidate)
        replacement = f"ending {suffix}" if suffix else "the waybill number you provided"
        cleaned = re.sub(re.escape(candidate), replacement, cleaned, flags=re.IGNORECASE)
        digits_only = re.sub(r"\D", "", candidate)
        if len(digits_only) >= 8:
            cleaned = re.sub(re.escape(digits_only), replacement, cleaned, flags=re.IGNORECASE)
    cleaned = _LONG_NUMERIC_RE.sub("the waybill number you provided", cleaned)
    return cleaned[:600]


def _reply_repair_context(
    *,
    blocked_result: WebchatRuntimeReplyResult,
    provider_result: RuntimeAIProviderResult,
    body: str | None,
    tracking_number: str | None,
) -> dict[str, Any]:
    return {
        "mode": "customer_reply_privacy_repair",
        "violation_codes": sorted(_policy_violation_codes(blocked_result)),
        "previous_intent": blocked_result.intent or provider_result.intent,
        "previous_reply_redacted": _redact_reply_for_repair_context(provider_result.reply, body=body, tracking_number=tracking_number),
        "requirements": [
            "Preserve the customer-service meaning of the previous reply.",
            "Remove raw tracking, waybill, phone-like, or secret-like identifiers from customer_reply.",
            "Use 'the waybill number you provided' or suffix-only references such as 'ending 011425'.",
            "Do not claim live parcel status unless tracking_fact_evidence_present=true.",
            "For no-evidence tracking, keep intent=tracking_unresolved and tracking_number=null.",
        ],
    }


def _runtime_context_with_reply_repair(
    runtime_context: dict[str, Any] | None,
    *,
    blocked_result: WebchatRuntimeReplyResult,
    provider_result: RuntimeAIProviderResult,
    body: str | None,
    tracking_number: str | None,
) -> dict[str, Any] | None:
    base = dict(runtime_context or {})
    base["reply_repair"] = _reply_repair_context(
        blocked_result=blocked_result,
        provider_result=provider_result,
        body=body,
        tracking_number=tracking_number,
    )
    return base


def _provider_result_with_local_privacy_repair(
    provider_result: RuntimeAIProviderResult,
    *,
    body: str | None,
    tracking_number: str | None,
) -> RuntimeAIProviderResult | None:
    repaired_reply = _redact_reply_for_repair_context(
        provider_result.reply,
        body=body,
        tracking_number=tracking_number,
    )
    if not repaired_reply or repaired_reply == (provider_result.reply or ""):
        return None
    summary = dict(provider_result.raw_payload_safe_summary or {})
    summary["local_privacy_repair_applied"] = True
    summary["local_privacy_repair_reason"] = "redact_raw_identifier_from_runtime_reply"
    return RuntimeAIProviderResult(
        **{
            **provider_result.__dict__,
            "reply": repaired_reply,
            "tracking_number": None,
            "raw_payload_safe_summary": summary,
        }
    )


def _looks_like_safe_tracking_reference(value: str | None) -> bool:
    cleaned = str(value or "").strip().lower()
    if not cleaned:
        return False
    return any(
        marker in cleaned
        for marker in (
            "parcel ending",
            "tracking number ending",
            "tracking reference ending",
            "tracking_reference_ending",
            "运单尾号",
            "单号尾号",
        )
    )


def _tracking_number_for_policy(*, body: str | None, tracking_fact_metadata: dict[str, Any] | None, provider_result: RuntimeAIProviderResult | None = None) -> str | None:
    def valid_candidate(value: str | None) -> str | None:
        cleaned = str(value or "").strip()
        if not cleaned:
            return None
        body_text = str(body or "").strip()
        if body_text == cleaned or TRACKING_CONTEXT_RE.search(body_text) or _looks_like_tracking_identifier(cleaned):
            return cleaned
        return None

    if isinstance(tracking_fact_metadata, dict):
        raw = tracking_fact_metadata.get("tracking_number")
        if isinstance(raw, str):
            candidate = valid_candidate(raw)
            if candidate:
                return candidate
    if provider_result and provider_result.tracking_number and not _looks_like_safe_tracking_reference(provider_result.tracking_number):
        candidate = valid_candidate(provider_result.tracking_number)
        if candidate:
            return candidate
    candidates = _tracking_reference_candidates(body=body, tracking_number=None, reply=None)
    for raw_candidate in candidates:
        candidate = valid_candidate(raw_candidate)
        if candidate:
            return candidate
    return None


def _mark_privacy_repaired_result(result: WebchatRuntimeReplyResult) -> WebchatRuntimeReplyResult:
    reply_source = result.reply_source or "provider_runtime"
    repaired_source = reply_source if reply_source.endswith(":repaired") else f"{reply_source}:repaired"
    trace = dict(result.ai_decision_trace or {})
    trace["reply_source"] = repaired_source
    trace["repair_applied"] = True
    trace["repair_reason"] = "customer_reply_privacy_policy_repair"
    return WebchatRuntimeReplyResult(
        **{
            **asdict(result),
            "reply_source": repaired_source,
            "ai_decision_trace": trace,
        }
    )


def _apply_grounding(
    *,
    provider_result: RuntimeAIProviderResult,
    runtime_context: dict[str, Any] | None,
) -> RuntimeAIProviderResult:
    safe_summary = dict(provider_result.raw_payload_safe_summary or {})
    if runtime_context:
        safe_summary.setdefault("rag_trace", summarize_rag_trace(runtime_context))
    knowledge = runtime_context.get("knowledge_context") if isinstance(runtime_context, dict) else None
    if isinstance(knowledge, dict) and knowledge.get("locked_facts"):
        safe_summary.setdefault("grounding_applied", False)
        safe_summary.setdefault("grounding_reason", "ai_runtime_reply_checked_against_locked_facts")
        safe_summary.setdefault("locked_fact_ids", [
            str(fact.get("item_key"))
            for fact in knowledge.get("locked_facts", [])
            if isinstance(fact, dict) and fact.get("item_key")
        ])
    return _provider_result_with_summary(provider_result, safe_summary)


async def generate_webchat_runtime_reply(
    *,
    tenant_key: str,
    channel_key: str,
    session_id: str,
    body: str,
    recent_context: list[dict[str, Any]] | None,
    request_id: str | None = None,
    tracking_fact_summary: str | None = None,
    tracking_fact_metadata: dict[str, Any] | None = None,
    tracking_fact_evidence_present: bool = False,
    market_id: int | None = None,
    language: str | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> WebchatRuntimeReplyResult:
    settings = get_webchat_runtime_settings()
    if not settings.enabled:
        result = WebchatRuntimeReplyResult(
            ok=False,
            ai_generated=False,
            reply_source=None,
            reply=None,
            intent=None,
            tracking_number=None,
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            ticket_creation_queued=False,
            elapsed_ms=0,
            error_code="ai_unavailable",
            retry_after_ms=1500,
        )
        record_webchat_runtime_metric(status="ai_unavailable", elapsed_ms=0)
        return result

    evidence_present = bool(tracking_fact_evidence_present and tracking_fact_summary)
    policy_tracking_fact_metadata = _tracking_metadata_for_policy(
        metadata=tracking_fact_metadata,
        evidence_present=evidence_present,
    )
    tracking_number_for_policy = _tracking_number_for_policy(
        body=body,
        tracking_fact_metadata=tracking_fact_metadata,
    )
    language_decision = detect_customer_language(body, explicit=language)
    target_language = language_decision.language
    latency_class = _latency_class_for_request(body=body, evidence_present=evidence_present)
    runtime_context = (
        runtime_context
        if isinstance(runtime_context, dict)
        else (
            _trusted_tracking_runtime_context()
            if evidence_present
            else _runtime_context_for_request(
                tenant_key=tenant_key,
                channel_key=channel_key,
                body=body,
                market_id=market_id,
                language=target_language,
                tracking_number=tracking_number_for_policy,
                tracking_fact_evidence_present=evidence_present,
            )
        )
    )
    latency_class = _latency_class_with_runtime_context(
        latency_class,
        body=body,
        runtime_context=runtime_context,
        evidence_present=evidence_present,
    )
    runtime_context = _runtime_context_with_latency_profile(runtime_context, latency_class=latency_class)
    runtime_context = _runtime_context_with_language_policy(
        runtime_context,
        language=target_language,
        source=language_decision.source,
        confidence=language_decision.confidence,
    )
    provider_request = RuntimeAIProviderRequest(
        tenant_key=tenant_key,
        channel_key=channel_key,
        session_id=session_id,
        body=body,
        recent_context=recent_context,
        request_id=request_id,
        tracking_fact_summary=tracking_fact_summary if evidence_present else None,
        tracking_fact_metadata=tracking_fact_metadata,
        tracking_fact_evidence_present=evidence_present,
        market_id=market_id,
        language=target_language,
        metadata=runtime_context,
    )
    provider_result = await dispatch_webchat_runtime_reply(request=provider_request)

    if provider_result.ok:
        provider_result = _apply_grounding(
            provider_result=provider_result,
            runtime_context=runtime_context,
        )

    result = _result_from_provider(
        provider_result,
        tracking_fact_metadata=policy_tracking_fact_metadata,
        tracking_number=_tracking_number_for_policy(
            body=body,
            tracking_fact_metadata=tracking_fact_metadata,
            provider_result=provider_result,
        ),
        runtime_context=runtime_context,
        tenant_key=tenant_key,
        channel_key=channel_key,
        session_id=session_id,
        request_id=request_id,
        body=body,
    )
    if _policy_blocked_only_by_repairable_privacy(result):
        tracking_number_for_repair = _tracking_number_for_policy(
            body=body,
            tracking_fact_metadata=tracking_fact_metadata,
            provider_result=provider_result,
        )
        locally_repaired_provider_result = _provider_result_with_local_privacy_repair(
            provider_result,
            body=body,
            tracking_number=tracking_number_for_repair,
        )
        if locally_repaired_provider_result is not None:
            locally_repaired_result = _result_from_provider(
                locally_repaired_provider_result,
                tracking_fact_metadata=policy_tracking_fact_metadata,
                tracking_number=tracking_number_for_repair,
                runtime_context=runtime_context,
                tenant_key=tenant_key,
                channel_key=channel_key,
                session_id=session_id,
                request_id=request_id,
                body=body,
            )
            if locally_repaired_result.ok:
                result = _mark_privacy_repaired_result(locally_repaired_result)
                status = "ok" if result.ok else (result.error_code or provider_result.error_code or "ai_unavailable")
                record_webchat_runtime_metric(
                    status=status,
                    intent=result.intent,
                    handoff_required=result.handoff_required,
                    elapsed_ms=result.elapsed_ms,
                )
                return result
        repair_runtime_context = _runtime_context_with_reply_repair(
            runtime_context,
            blocked_result=result,
            provider_result=provider_result,
            body=body,
            tracking_number=tracking_number_for_repair,
        )
        repair_request = RuntimeAIProviderRequest(
            tenant_key=tenant_key,
            channel_key=channel_key,
            session_id=session_id,
            body=body,
            recent_context=recent_context,
            request_id=request_id,
            tracking_fact_summary=tracking_fact_summary if evidence_present else None,
            tracking_fact_metadata=tracking_fact_metadata,
            tracking_fact_evidence_present=evidence_present,
            market_id=market_id,
            language=target_language,
            metadata=repair_runtime_context,
        )
        repair_provider_result = await dispatch_webchat_runtime_reply(request=repair_request)
        if repair_provider_result.ok:
            repair_provider_result = _apply_grounding(
                provider_result=repair_provider_result,
                runtime_context=repair_runtime_context,
            )
        repaired_result = _result_from_provider(
            repair_provider_result,
            tracking_fact_metadata=policy_tracking_fact_metadata,
            tracking_number=_tracking_number_for_policy(
                body=body,
                tracking_fact_metadata=tracking_fact_metadata,
                provider_result=repair_provider_result,
            ),
            runtime_context=repair_runtime_context,
            tenant_key=tenant_key,
            channel_key=channel_key,
            session_id=session_id,
            request_id=request_id,
            body=body,
        )
        if repaired_result.ok:
            result = _mark_privacy_repaired_result(repaired_result)
    status = "ok" if result.ok else (result.error_code or provider_result.error_code or "ai_unavailable")
    record_webchat_runtime_metric(
        status=status,
        intent=result.intent,
        handoff_required=result.handoff_required,
        elapsed_ms=result.elapsed_ms,
    )
    return result
