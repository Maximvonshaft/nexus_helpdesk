from __future__ import annotations

import json
import os
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..enums import EventType
from ..models import Customer, Ticket, TicketEvent
from ..models_agent_routing import ConversationControl
from ..utils.time import utc_now
from ..webchat_models import (
    WebchatConversation,
    WebchatHandoffRequest,
    WebchatMessage,
)
from .agent_availability_service import availability_summary
from .agent_integration_service import (
    execute_integration_operation,
    list_integration_catalog,
)
from .agent_runtime.execution_scope import current_agent_release_snapshot
from .agent_runtime.specialist_runtime import run_specialist_sync
from .background_jobs import enqueue_speedaf_work_order_create_job
from .knowledge_release_retrieval import retrieve_release_published_chunks
from .nexus_osr.controlled_action_executor import (
    ActionExecutionRequest,
    ActionExecutionResult,
    ActionHandler,
)
from .speedaf.status_map import is_auto_work_order_type_allowed

_SPECIALIST_OBJECTIVES = {
    "investigate_current_request": (
        "Investigate the current customer request using only the supplied case "
        "context and evidence references. Identify supported facts, uncertainty "
        "and what the parent Agent should verify next."
    ),
    "check_policy_consistency": (
        "Check whether the current case and proposed handling are consistent "
        "with the available enterprise policy evidence. Surface conflicts and "
        "human-review boundaries."
    ),
    "summarize_current_case": (
        "Produce a concise operational case summary for the parent Agent, "
        "separating known facts, unresolved points and recommended next review."
    ),
    "review_current_translation": (
        "Review the current customer-language text for meaning preservation, "
        "tone and ambiguity. Do not reproduce identifiers or provide a customer "
        "reply; provide evidence to the parent Agent only."
    ),
    "analyze_available_data": (
        "Analyze the bounded data visible in the current case, identify patterns "
        "and limitations, and return evidence without inventing missing values."
    ),
}


def build_agent_tool_handlers(
    db: Session,
    *,
    conversation: WebchatConversation | None,
    ticket: Ticket | None,
    customer: Customer | None,
) -> dict[str, ActionHandler]:
    """Build request-local handlers consumed by the one canonical executor."""

    def knowledge_search(request: ActionExecutionRequest) -> ActionExecutionResult:
        query = str(request.action.arguments.get("query") or "").strip()
        limit = max(1, min(int(request.action.arguments.get("limit") or 5), 8))
        retrieval = retrieve_release_published_chunks(
            db,
            q=query,
            tenant_id=str(request.audit_context.get("tenant_id") or "default"),
            market_id=getattr(ticket, "market_id", None),
            channel=request.channel,
            audience_scope="customer",
            language=None,
            limit=limit,
        )
        if retrieval is None:
            return _failure(
                request,
                "agent_release_snapshot_required_for_knowledge",
            )
        hits: list[dict[str, Any]] = []
        for hit in retrieval.hits[:limit]:
            answer = str(hit.direct_answer or hit.text or "").strip()
            if answer:
                hits.append(
                    {
                        "source_id": str(hit.item_key)[:180],
                        "title": str(hit.title)[:180],
                        "answer": answer[:1200],
                        "answer_mode": hit.answer_mode,
                    }
                )
        return ActionExecutionResult(
            ok=bool(hits),
            tool_name=request.action.tool_name,
            status="executed" if hits else "no_results",
            summary={"query": query[:240], "hits": hits, "count": len(hits)},
            customer_visible_summary=(
                None if hits else "No approved knowledge result was found."
            ),
            case_context=request.case_context,
            error_code=None if hits else "knowledge_not_found",
        )

    def support_availability(
        request: ActionExecutionRequest,
    ) -> ActionExecutionResult:
        if conversation is None:
            return _failure(request, "conversation_required")
        control = (
            db.query(ConversationControl)
            .filter(ConversationControl.conversation_id == conversation.id)
            .first()
        )
        if control is None:
            return _failure(request, "conversation_control_required")
        request_row = (
            db.get(
                WebchatHandoffRequest,
                conversation.current_handoff_request_id,
            )
            if conversation.current_handoff_request_id is not None
            else None
        )
        if request_row is not None and request_row.conversation_id != conversation.id:
            request_row = None
        summary = availability_summary(
            db,
            tenant_key=control.tenant_key,
            country_code=control.country_code,
            channel_key=control.channel_key,
            request_row=request_row,
            conversation_id=conversation.id,
        )
        return ActionExecutionResult(
            ok=True,
            tool_name=request.action.tool_name,
            status="executed",
            summary=summary,
            customer_visible_summary=_availability_customer_summary(summary),
            case_context=request.case_context,
        )

    def integration_search(request: ActionExecutionRequest) -> ActionExecutionResult:
        arguments = request.action.arguments
        keywords = _integration_keywords(arguments.get("keywords"))
        mode = str(arguments.get("mode") or "all").strip().lower()
        limit = max(1, min(int(arguments.get("limit") or 8), 20))
        if not keywords or mode not in {"read", "write", "all"}:
            return _failure(request, "integration_search_arguments_invalid")
        snapshot = current_agent_release_snapshot()
        if not isinstance(snapshot, dict):
            return _failure(
                request,
                "agent_release_snapshot_required_for_integration",
            )
        try:
            catalog = list_integration_catalog(
                db,
                market_id=getattr(ticket, "market_id", None),
                channel=request.channel,
                release_snapshot=snapshot,
            )
        except RuntimeError:
            return _failure(
                request,
                "agent_release_snapshot_required_for_integration",
            )
        matches: list[dict[str, Any]] = []
        for integration in catalog:
            integration_key = str(integration.get("resource_key") or "")[:160]
            integration_name = str(integration.get("name") or "")[:160]
            for operation in integration.get("operations") or []:
                if not isinstance(operation, dict) or operation.get("enabled") is False:
                    continue
                operation_mode = str(operation.get("mode") or "").lower()
                if operation_mode not in {"read", "write"}:
                    continue
                if mode != "all" and operation_mode != mode:
                    continue
                operation_key = str(operation.get("key") or "")[:160]
                description = str(operation.get("description") or "")[:600]
                searchable = " ".join(
                    (integration_key, integration_name, operation_key, description)
                ).lower()
                score = sum(1 for keyword in keywords if keyword in searchable)
                if score <= 0:
                    continue
                matches.append(
                    {
                        "integration_key": integration_key,
                        "operation": operation_key,
                        "description": description,
                        "mode": operation_mode,
                        "risk_level": str(operation.get("risk_level") or "medium")[:20],
                        "requires_confirmation": bool(
                            operation.get("requires_confirmation")
                        ),
                        "score": score,
                    }
                )
        matches.sort(
            key=lambda item: (
                -int(item["score"]),
                str(item["integration_key"]),
                str(item["operation"]),
            )
        )
        selected = matches[:limit]
        return ActionExecutionResult(
            ok=bool(selected),
            tool_name=request.action.tool_name,
            status="executed" if selected else "no_results",
            summary={"matches": selected, "count": len(selected)},
            customer_visible_summary=None,
            case_context=request.case_context,
            error_code=None if selected else "integration_search_no_results",
        )

    def integration_read(request: ActionExecutionRequest) -> ActionExecutionResult:
        return _integration(request, expected_write=False)

    def integration_write(request: ActionExecutionRequest) -> ActionExecutionResult:
        return _integration(request, expected_write=True)

    def _integration(
        request: ActionExecutionRequest,
        *,
        expected_write: bool,
    ) -> ActionExecutionResult:
        arguments = request.action.arguments
        try:
            result = execute_integration_operation(
                db,
                integration_key=str(arguments.get("integration_key") or ""),
                operation=str(arguments.get("operation") or ""),
                arguments=(
                    arguments.get("arguments")
                    if isinstance(arguments.get("arguments"), dict)
                    else {}
                ),
                expected_write=expected_write,
                market_id=getattr(ticket, "market_id", None),
                channel=request.channel,
                release_snapshot=current_agent_release_snapshot(),
            )
        except HTTPException as exc:
            return _failure(request, _detail(exc))
        except RuntimeError:
            return _failure(
                request,
                "agent_release_snapshot_required_for_integration",
            )
        return ActionExecutionResult(
            result.ok,
            request.action.tool_name,
            result.status,
            summary=result.safe_summary(),
            customer_visible_summary=None,
            case_context=request.case_context,
            error_code=result.error_code,
        )

    def specialist_delegate(
        request: ActionExecutionRequest,
    ) -> ActionExecutionResult:
        arguments = request.action.arguments
        specialist = str(arguments.get("specialist") or "").strip().lower()
        objective = str(arguments.get("objective") or "").strip().lower()
        objective_instruction = _SPECIALIST_OBJECTIVES.get(objective)
        if objective_instruction is None:
            return _failure(request, "specialist_objective_invalid")
        release_snapshot = current_agent_release_snapshot()
        if not isinstance(release_snapshot, dict):
            return _failure(
                request,
                "agent_release_snapshot_required_for_specialist",
            )
        task = _specialist_task(
            db,
            objective_instruction=objective_instruction,
            conversation=conversation,
            ticket=ticket,
        )
        evidence_refs = arguments.get("evidence_refs")
        if not isinstance(evidence_refs, list):
            evidence_refs = []
        session_id = str(
            getattr(conversation, "public_id", None)
            or getattr(conversation, "id", None)
            or getattr(ticket, "id", None)
            or "specialist"
        )[:160]
        request_id = str(
            request.idempotency_key
            or f"specialist:{session_id}:{specialist}:{objective}"
        )[:160]
        try:
            result = run_specialist_sync(
                db,
                release_snapshot=release_snapshot,
                tenant_key=str(
                    request.audit_context.get("tenant_id") or "default"
                )[:80],
                channel_key=str(request.channel or "webchat")[:40],
                session_id=session_id,
                request_id=request_id,
                specialist=specialist,
                task=task,
                evidence_refs=[str(item)[:160] for item in evidence_refs[:20]],
            )
        except (RuntimeError, ValueError):
            return _failure(request, "specialist_execution_failed")
        return ActionExecutionResult(
            ok=result.ok,
            tool_name=request.action.tool_name,
            status=result.status,
            summary=result.safe_summary(),
            customer_visible_summary=None,
            case_context=request.case_context,
            error_code=result.error_code,
        )

    def work_order_create(request: ActionExecutionRequest) -> ActionExecutionResult:
        if ticket is None:
            return _failure(request, "ticket_required")
        if not _enabled("SPEEDAF_WORK_ORDER_CREATE_ENABLED"):
            return _failure(request, "speedaf_work_order_create_disabled")
        arguments = request.action.arguments
        tracking_number = str(
            arguments.get("tracking_number") or ticket.tracking_number or ""
        ).strip().upper()
        work_order_type = str(
            arguments.get("work_order_type") or "WT0103-05"
        ).strip()[:32]
        description = " ".join(
            str(
                arguments.get("description")
                or "Delivery follow-up requested."
            ).split()
        )[:200]
        caller_id = str(
            getattr(customer, "phone", None)
            or ticket.preferred_reply_contact
            or ""
        ).strip()[:80]
        if not tracking_number:
            return _failure(request, "tracking_number_required")
        if not caller_id:
            return _failure(request, "customer_contact_required")
        if not is_auto_work_order_type_allowed(work_order_type):
            return _failure(
                request,
                "speedaf_work_order_type_not_allowed",
            )
        job = enqueue_speedaf_work_order_create_job(
            db,
            ticket_id=ticket.id,
            waybill_code=tracking_number,
            caller_id=caller_id,
            description=description,
            work_order_type=work_order_type,
        )
        db.add(
            TicketEvent(
                ticket_id=ticket.id,
                actor_id=None,
                event_type=EventType.field_updated,
                field_name="speedaf_work_order",
                new_value="queued",
                note="Speedaf delivery follow-up work order queued by the Agent.",
                payload_json=json.dumps(
                    {
                        "job_id": job.id,
                        "work_order_type": work_order_type,
                        "source": "agent_tool",
                    },
                    separators=(",", ":"),
                ),
                created_at=utc_now(),
            )
        )
        return ActionExecutionResult(
            True,
            request.action.tool_name,
            "queued",
            summary={
                "job_id": job.id,
                "dedupe_key": job.dedupe_key,
                "work_order_type": work_order_type,
            },
            customer_visible_summary="The delivery follow-up request was queued.",
            case_context=request.case_context,
        )

    return {
        "knowledge.search": knowledge_search,
        "support.availability": support_availability,
        "integration.search": integration_search,
        "integration.read": integration_read,
        "integration.write": integration_write,
        "specialist.delegate": specialist_delegate,
        "speedaf.workOrder.create": work_order_create,
    }


def extension_executable_tool_names() -> tuple[str, ...]:
    return (
        "integration.search",
        "integration.read",
        "integration.write",
        "specialist.delegate",
        "speedaf.workOrder.create",
    )


def _availability_customer_summary(summary: dict[str, Any]) -> str:
    if summary.get("available"):
        return "A support specialist is available now."
    wait_range = summary.get("estimated_wait_range_seconds")
    if isinstance(wait_range, dict):
        lower = max(0, int(wait_range.get("min") or 0))
        upper = max(lower, int(wait_range.get("max") or lower))
        if upper > 0:
            return (
                "All eligible specialists are currently busy. "
                f"The evidence-based wait estimate is about {lower // 60}–"
                f"{max(1, (upper + 59) // 60)} minutes."
            )
    return (
        "All eligible specialists are currently busy, and there is not yet "
        "enough recent evidence to provide a reliable wait estimate."
    )


def _integration_keywords(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for raw in value[:8]:
        cleaned = str(raw or "").strip().lower()[:80]
        if cleaned and cleaned not in output:
            output.append(cleaned)
    return output


def _specialist_task(
    db: Session,
    *,
    objective_instruction: str,
    conversation: WebchatConversation | None,
    ticket: Ticket | None,
) -> str:
    current_request = ""
    if conversation is not None:
        row = (
            db.query(WebchatMessage)
            .filter(
                WebchatMessage.conversation_id == conversation.id,
                WebchatMessage.direction == "visitor",
            )
            .order_by(
                WebchatMessage.created_at.desc(),
                WebchatMessage.id.desc(),
            )
            .first()
        )
        if row is not None:
            current_request = " ".join(
                str(row.body_text or "").split()
            )[:2000]
    ticket_context = {
        "ticket_present": ticket is not None,
        "status": str(getattr(ticket, "status", "") or "")[:40] or None,
        "priority": str(getattr(ticket, "priority", "") or "")[:40] or None,
        "issue_type": str(getattr(ticket, "issue_type", "") or "")[:120] or None,
        "market_id": getattr(ticket, "market_id", None),
    }
    return (
        f"{objective_instruction}\n"
        f"Current request: {current_request or 'No current customer text is available.'}\n"
        f"Bounded case metadata: {json.dumps(ticket_context, ensure_ascii=False, separators=(',', ':'))}"
    )[:3000]


def _failure(
    request: ActionExecutionRequest,
    error_code: str,
) -> ActionExecutionResult:
    return ActionExecutionResult(
        False,
        request.action.tool_name,
        "failed",
        summary={},
        case_context=request.case_context,
        error_code=error_code[:120],
    )


def _detail(exc: HTTPException) -> str:
    detail: Any = exc.detail
    if isinstance(detail, dict):
        detail = (
            detail.get("error_code")
            or detail.get("detail")
            or "request_failed"
        )
    return str(detail or "request_failed")[:120]


def _enabled(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}
