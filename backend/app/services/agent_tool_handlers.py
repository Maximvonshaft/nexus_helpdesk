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
from .agent_runtime.execution_scope import current_agent_release_snapshot
from .background_jobs import enqueue_speedaf_work_order_create_job
from .integration_runtime import execute_integration_operation
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
