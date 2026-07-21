from __future__ import annotations

import json
import os
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..enums import EventType
from ..models import Customer, Ticket, TicketEvent
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation
from .agent_integration_service import execute_integration_operation
from .agent_runtime.execution_scope import current_agent_release_snapshot
from .background_jobs import enqueue_speedaf_work_order_create_job
from .knowledge_release_retrieval import retrieve_release_published_chunks
from .nexus_osr.controlled_action_executor import (
    ActionExecutionRequest,
    ActionExecutionResult,
    ActionHandler,
)
from .speedaf.status_map import is_auto_work_order_type_allowed


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
            return _failure(request, "agent_release_snapshot_required_for_knowledge")
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
            return _failure(request, "agent_release_snapshot_required_for_integration")
        return ActionExecutionResult(
            result.ok,
            request.action.tool_name,
            result.status,
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
        work_order_type = str(arguments.get("work_order_type") or "WT0103-05").strip()[:32]
        description = " ".join(
            str(arguments.get("description") or "Delivery follow-up requested.").split()
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
            return _failure(request, "speedaf_work_order_type_not_allowed")
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
        "integration.read": integration_read,
        "integration.write": integration_write,
        "speedaf.workOrder.create": work_order_create,
    }


def extension_executable_tool_names() -> tuple[str, ...]:
    return (
        "integration.read",
        "integration.write",
        "speedaf.workOrder.create",
    )


def _failure(request: ActionExecutionRequest, error_code: str) -> ActionExecutionResult:
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
        detail = detail.get("error_code") or detail.get("detail") or "request_failed"
    return str(detail or "request_failed")[:120]


def _enabled(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}
