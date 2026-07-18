from __future__ import annotations

from typing import Any, Callable, TypeVar

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas_osr_admin import (
    CaseContextSafeUpdate,
    EscalationPolicyCreate,
    EscalationPolicyUpdate,
    HumanHoursPolicyCreate,
    HumanHoursPolicyUpdate,
    ToolExecutionPolicyCreate,
    ToolExecutionPolicyUpdate,
    WhatsAppRoutingRuleCreate,
    WhatsAppRoutingRuleUpdate,
)
from ..services.nexus_osr.admin_service import (
    control_tower_summary,
    create_policy_record,
    delete_policy_record,
    get_case_context,
    get_policy_record,
    get_runtime_audit,
    list_case_contexts,
    list_policy_records,
    list_runtime_audits,
    normalize_tenant_id,
    preview_escalation_policy,
    preview_human_hours_policy,
    preview_tool_execution_policy,
    preview_whatsapp_routing_rule,
    update_case_context_safe_fields,
    update_policy_record,
)
from ..services.nexus_osr.admin_views import build_osr_debug_snapshot
from ..services.permissions import ensure_can_manage_runtime
from .deps import get_current_user


def _safe_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for item in errors[:50]:
        safe.append(
            {
                "type": str(item.get("type") or "validation_error")[:120],
                "loc": [str(part)[:120] for part in item.get("loc") or []],
                "msg": str(item.get("msg") or "Invalid request")[:240],
            }
        )
    return safe


class RedactedValidationRoute(APIRoute):
    """Prevent FastAPI/Pydantic from echoing hostile request values in 422 bodies."""

    def get_route_handler(self) -> Callable:
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request):
            try:
                return await original_route_handler(request)
            except RequestValidationError as exc:
                return JSONResponse(
                    status_code=422,
                    content={"detail": _safe_validation_errors(exc.errors())},
                )

        return custom_route_handler


router = APIRouter(
    prefix="/api/admin/osr",
    tags=["admin-osr"],
    route_class=RedactedValidationRoute,
)


def _ensure_osr_admin(current_user: Any, db: Session) -> None:
    """Use the runtime-management capability as the only OSR admin authority."""

    ensure_can_manage_runtime(current_user, db)


def _tenant_scope(x_nexus_tenant: str = Header(alias="X-Nexus-Tenant")) -> str:
    return normalize_tenant_id(x_nexus_tenant)


def _create_payload(payload: Any) -> dict[str, Any]:
    return payload.model_dump(exclude_none=True)


def _update_payload(payload: Any) -> dict[str, Any]:
    return payload.model_dump(exclude_unset=True, exclude_none=True)


WriteResult = TypeVar("WriteResult")


def _safe_write(
    db: Session,
    operation: Callable[[], WriteResult],
    *,
    conflict_code: str = "osr_admin_record_conflict",
) -> WriteResult:
    try:
        result = operation()
        db.commit()
        return result
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=conflict_code,
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="osr_admin_write_failed",
        ) from exc


@router.get("/human-hours-policies")
def list_human_hours_policies(
    country_code: str | None = None,
    channel: str | None = None,
    queue_key: str | None = None,
    enabled: bool | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return list_policy_records(
        db,
        "human_hours",
        filters={
            "country_code": country_code,
            "channel": channel,
            "queue_key": queue_key,
            "enabled": enabled,
        },
        limit=limit,
        offset=offset,
    )


@router.post("/human-hours-policies", status_code=status.HTTP_201_CREATED)
def create_human_hours_policy(
    payload: HumanHoursPolicyCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return _safe_write(
        db,
        lambda: create_policy_record(db, "human_hours", _create_payload(payload)),
    )


@router.get("/human-hours-policies/{record_id}")
def get_human_hours_policy(
    record_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return get_policy_record(db, "human_hours", record_id)


@router.patch("/human-hours-policies/{record_id}")
def update_human_hours_policy(
    record_id: int,
    payload: HumanHoursPolicyUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return _safe_write(
        db,
        lambda: update_policy_record(
            db,
            "human_hours",
            record_id,
            _update_payload(payload),
        ),
    )


@router.delete("/human-hours-policies/{record_id}")
def delete_human_hours_policy(
    record_id: int,
    response: Response,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    result = _safe_write(
        db,
        lambda: delete_policy_record(db, "human_hours", record_id),
    )
    response.status_code = status.HTTP_200_OK
    return result


@router.get("/escalation-policies")
def list_escalation_policies(
    risk_key: str | None = None,
    country_code: str | None = None,
    channel: str | None = None,
    enabled: bool | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return list_policy_records(
        db,
        "escalation",
        filters={
            "risk_key": risk_key,
            "country_code": country_code,
            "channel": channel,
            "enabled": enabled,
        },
        limit=limit,
        offset=offset,
    )


@router.post("/escalation-policies", status_code=status.HTTP_201_CREATED)
def create_escalation_policy(
    payload: EscalationPolicyCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return _safe_write(
        db,
        lambda: create_policy_record(db, "escalation", _create_payload(payload)),
    )


@router.get("/escalation-policies/{record_id}")
def get_escalation_policy(
    record_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return get_policy_record(db, "escalation", record_id)


@router.patch("/escalation-policies/{record_id}")
def update_escalation_policy(
    record_id: int,
    payload: EscalationPolicyUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return _safe_write(
        db,
        lambda: update_policy_record(
            db,
            "escalation",
            record_id,
            _update_payload(payload),
        ),
    )


@router.delete("/escalation-policies/{record_id}")
def delete_escalation_policy(
    record_id: int,
    response: Response,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    result = _safe_write(
        db,
        lambda: delete_policy_record(db, "escalation", record_id),
    )
    response.status_code = status.HTTP_200_OK
    return result


@router.get("/tool-execution-policies")
def list_tool_execution_policies(
    tool_name: str | None = None,
    country_code: str | None = None,
    channel: str | None = None,
    enabled: bool | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return list_policy_records(
        db,
        "tool_execution",
        filters={
            "tool_name": tool_name,
            "country_code": country_code,
            "channel": channel,
            "enabled": enabled,
        },
        limit=limit,
        offset=offset,
    )


@router.post("/tool-execution-policies", status_code=status.HTTP_201_CREATED)
def create_tool_execution_policy(
    payload: ToolExecutionPolicyCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return _safe_write(
        db,
        lambda: create_policy_record(db, "tool_execution", _create_payload(payload)),
    )


@router.get("/tool-execution-policies/{record_id}")
def get_tool_execution_policy(
    record_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return get_policy_record(db, "tool_execution", record_id)


@router.patch("/tool-execution-policies/{record_id}")
def update_tool_execution_policy(
    record_id: int,
    payload: ToolExecutionPolicyUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return _safe_write(
        db,
        lambda: update_policy_record(
            db,
            "tool_execution",
            record_id,
            _update_payload(payload),
        ),
    )


@router.delete("/tool-execution-policies/{record_id}")
def delete_tool_execution_policy(
    record_id: int,
    response: Response,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    result = _safe_write(
        db,
        lambda: delete_policy_record(db, "tool_execution", record_id),
    )
    response.status_code = status.HTTP_200_OK
    return result


@router.get("/whatsapp-routing-rules")
def list_whatsapp_routing_rules(
    country_code: str | None = None,
    issue_type: str | None = None,
    channel: str | None = None,
    enabled: bool | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return list_policy_records(
        db,
        "whatsapp_routing",
        filters={
            "country_code": country_code,
            "issue_type": issue_type,
            "channel": channel,
            "enabled": enabled,
        },
        limit=limit,
        offset=offset,
    )


@router.post("/whatsapp-routing-rules", status_code=status.HTTP_201_CREATED)
def create_whatsapp_routing_rule(
    payload: WhatsAppRoutingRuleCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return _safe_write(
        db,
        lambda: create_policy_record(
            db,
            "whatsapp_routing",
            _create_payload(payload),
        ),
        conflict_code="whatsapp_routing_rule_conflict",
    )


@router.get("/whatsapp-routing-rules/{record_id}")
def get_whatsapp_routing_rule(
    record_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return get_policy_record(db, "whatsapp_routing", record_id)


@router.patch("/whatsapp-routing-rules/{record_id}")
def update_whatsapp_routing_rule(
    record_id: int,
    payload: WhatsAppRoutingRuleUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return _safe_write(
        db,
        lambda: update_policy_record(
            db,
            "whatsapp_routing",
            record_id,
            _update_payload(payload),
        ),
        conflict_code="whatsapp_routing_rule_conflict",
    )


@router.delete("/whatsapp-routing-rules/{record_id}")
def delete_whatsapp_routing_rule(
    record_id: int,
    response: Response,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    result = _safe_write(
        db,
        lambda: delete_policy_record(db, "whatsapp_routing", record_id),
    )
    response.status_code = status.HTTP_200_OK
    return result


@router.get("/policy-preview/human-hours")
def preview_human_hours(
    country_code: str | None = None,
    channel: str | None = None,
    queue_key: str = "support",
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return preview_human_hours_policy(
        db,
        country_code=country_code,
        channel=channel,
        queue_key=queue_key,
    )


@router.get("/policy-preview/escalation")
def preview_escalation(
    country_code: str | None = None,
    channel: str | None = None,
    message: str | None = None,
    ai_attempt_count: int = 0,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return preview_escalation_policy(
        db,
        country_code=country_code,
        channel=channel,
        message=message,
        ai_attempt_count=ai_attempt_count,
    )


@router.get("/policy-preview/tool-execution")
def preview_tool_execution(
    tool_name: str,
    country_code: str | None = None,
    channel: str | None = None,
    has_tracking_number: bool = False,
    has_contact: bool = False,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return preview_tool_execution_policy(
        db,
        tool_name=tool_name,
        country_code=country_code,
        channel=channel,
        has_tracking_number=has_tracking_number,
        has_contact=has_contact,
    )


@router.get("/policy-preview/whatsapp-routing")
def preview_whatsapp_routing(
    country_code: str | None = None,
    issue_type: str | None = None,
    channel: str = "whatsapp",
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return preview_whatsapp_routing_rule(
        db,
        country_code=country_code,
        issue_type=issue_type,
        channel=channel,
    )


@router.get("/runtime-decision-audits")
def list_runtime_decision_audits(
    conversation_id: int | None = None,
    ticket_id: int | None = None,
    allowed: bool | None = None,
    channel: str | None = None,
    country_code: str | None = None,
    limit: int = 50,
    offset: int = 0,
    tenant_id: str = Depends(_tenant_scope),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return list_runtime_audits(
        db,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        ticket_id=ticket_id,
        allowed=allowed,
        channel=channel,
        country_code=country_code,
        limit=limit,
        offset=offset,
    )


@router.get("/runtime-decision-audits/{audit_id}")
def get_runtime_decision_audit(
    audit_id: int,
    tenant_id: str = Depends(_tenant_scope),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return get_runtime_audit(db, audit_id, tenant_id=tenant_id)


@router.get("/case-contexts")
def list_osr_case_contexts(
    conversation_id: int | None = None,
    ticket_id: int | None = None,
    status_filter: str | None = None,
    limit: int = 50,
    offset: int = 0,
    tenant_id: str = Depends(_tenant_scope),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return list_case_contexts(
        db,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        ticket_id=ticket_id,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )


@router.get("/case-contexts/{context_id}")
def get_osr_case_context(
    context_id: int,
    tenant_id: str = Depends(_tenant_scope),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return get_case_context(db, context_id, tenant_id=tenant_id)


@router.patch("/case-contexts/{context_id}")
def update_osr_case_context(
    context_id: int,
    payload: CaseContextSafeUpdate,
    tenant_id: str = Depends(_tenant_scope),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return _safe_write(
        db,
        lambda: update_case_context_safe_fields(
            db,
            context_id,
            payload.model_dump(exclude_unset=True, exclude_none=True),
            tenant_id=tenant_id,
        ),
    )


@router.get("/debug-snapshot")
def get_osr_debug_snapshot(
    conversation_id: int | None = None,
    ticket_id: int | None = None,
    tenant_id: str = Depends(_tenant_scope),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    if conversation_id is None and ticket_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="debug_snapshot_identifier_required",
        )
    return build_osr_debug_snapshot(
        db,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        ticket_id=ticket_id,
    )


@router.get("/control-tower/summary")
def get_osr_control_tower_summary(
    top_n: int = 10,
    tenant_id: str = Depends(_tenant_scope),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_osr_admin(current_user, db)
    return control_tower_summary(db, tenant_id=tenant_id, top_n=top_n)
