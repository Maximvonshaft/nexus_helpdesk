from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..db import get_current_request_id
from ..models import AIConfigResource, ChannelAccount, MarketBulletin, OutboundEmailAccount, Ticket
from ..models_control_plane import GovernanceReleaseEvent, GovernanceReleaseRequest, KnowledgeItem, PersonaProfile
from ..schemas_governance_release import GovernanceReleaseAction, GovernanceReleaseCreate, RELEASE_STATUSES
from ..utils.time import utc_now
from .audit_service import log_admin_audit


TERMINAL_STATUSES = {"published", "rolled_back", "rejected"}
TRANSITIONS: dict[str, set[str]] = {
    "submit": {"draft", "rejected"},
    "approve": {"pending_review"},
    "publish": {"approved"},
    "rollback": {"published"},
    "reject": {"pending_review", "approved"},
}
ACTION_STATUS = {
    "submit": "pending_review",
    "approve": "approved",
    "publish": "published",
    "rollback": "rolled_back",
    "reject": "rejected",
}


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    return text or None


def _source_exists(db: Session, source_type: str, source_id: int | None) -> dict[str, Any]:
    if source_id is None:
        return {"source_type": source_type, "source_id": None, "exists": True, "label": "ad-hoc governance request"}

    model_map: dict[str, tuple[Any, str]] = {
        "ai_config": (AIConfigResource, "name"),
        "bulletin": (MarketBulletin, "title"),
        "channel_account": (ChannelAccount, "display_name"),
        "outbound_email": (OutboundEmailAccount, "display_name"),
        "persona": (PersonaProfile, "name"),
        "knowledge": (KnowledgeItem, "title"),
        "speedaf_action": (Ticket, "ticket_no"),
    }
    model_info = model_map.get(source_type)
    if model_info is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="governance_release_unknown_source_type")
    model, label_attr = model_info
    row = db.query(model).filter(model.id == source_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="governance_release_source_not_found")
    label = getattr(row, label_attr, None) or getattr(row, "resource_key", None) or getattr(row, "item_key", None) or str(source_id)
    return {"source_type": source_type, "source_id": source_id, "exists": True, "label": label}


def _event(
    db: Session,
    row: GovernanceReleaseRequest,
    *,
    actor_id: int | None,
    event_type: str,
    note: str | None = None,
    payload: dict[str, Any] | None = None,
) -> GovernanceReleaseEvent:
    event = GovernanceReleaseEvent(
        release_id=row.id,
        actor_id=actor_id,
        event_type=event_type,
        note=_clean(note),
        payload_json=payload or {},
        request_id=get_current_request_id(),
    )
    db.add(event)
    db.flush()
    return event


def _audit(
    db: Session,
    row: GovernanceReleaseRequest,
    *,
    actor_id: int | None,
    action: str,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
) -> None:
    log_admin_audit(
        db,
        actor_id=actor_id,
        action=f"governance_release.{action}",
        target_type="governance_release",
        target_id=row.id,
        old_value=old_value,
        new_value=new_value,
    )


def _snapshot(row: GovernanceReleaseRequest) -> dict[str, Any]:
    return {
        "id": row.id,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "title": row.title,
        "release_type": row.release_type,
        "status": row.status,
        "risk_level": row.risk_level,
        "audit_target_type": row.audit_target_type,
        "audit_target_id": row.audit_target_id,
    }


def create_release_request(db: Session, payload: GovernanceReleaseCreate, actor) -> GovernanceReleaseRequest:
    source = _source_exists(db, payload.source_type, payload.source_id)
    now = utc_now()
    row = GovernanceReleaseRequest(
        source_type=payload.source_type,
        source_id=payload.source_id,
        title=payload.title,
        summary=payload.summary,
        release_type=payload.release_type,
        status=payload.status,
        risk_level=payload.risk_level,
        impact_json=payload.impact_json or {"source": source},
        diff_json=payload.diff_json or {},
        rollback_plan=payload.rollback_plan,
        audit_target_type=payload.audit_target_type or payload.source_type,
        audit_target_id=payload.audit_target_id or payload.source_id,
        requested_by=getattr(actor, "id", None),
        submitted_at=now if payload.status == "pending_review" else None,
    )
    db.add(row)
    db.flush()
    _event(
        db,
        row,
        actor_id=getattr(actor, "id", None),
        event_type="created",
        note="Governance release request created.",
        payload={"source": source, "initial_status": payload.status},
    )
    if payload.status == "pending_review":
        _event(
            db,
            row,
            actor_id=getattr(actor, "id", None),
            event_type="submitted",
            note="Governance release request submitted for review.",
            payload={"source": source},
        )
    _audit(db, row, actor_id=getattr(actor, "id", None), action="create", new_value=_snapshot(row))
    return row


def list_release_requests(
    db: Session,
    *,
    status_filter: str | None = None,
    source_type: str | None = None,
    risk_level: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[GovernanceReleaseRequest], int, dict[str, int], dict[str, int], dict[str, int]]:
    query = db.query(GovernanceReleaseRequest)
    if status_filter:
        query = query.filter(GovernanceReleaseRequest.status == status_filter)
    if source_type:
        query = query.filter(GovernanceReleaseRequest.source_type == source_type)
    if risk_level:
        query = query.filter(GovernanceReleaseRequest.risk_level == risk_level)
    if q:
        needle = f"%{q.strip()}%"
        query = query.filter(or_(GovernanceReleaseRequest.title.ilike(needle), GovernanceReleaseRequest.summary.ilike(needle)))
    total = query.count()
    rows = (
        query.order_by(GovernanceReleaseRequest.updated_at.desc(), GovernanceReleaseRequest.id.desc())
        .offset(max(offset, 0))
        .limit(min(max(limit, 1), 200))
        .all()
    )
    all_rows = db.query(
        GovernanceReleaseRequest.status,
        GovernanceReleaseRequest.source_type,
        GovernanceReleaseRequest.risk_level,
    ).all()
    status_counts = Counter(row.status for row in all_rows)
    source_counts = Counter(row.source_type for row in all_rows)
    risk_counts = Counter(row.risk_level for row in all_rows)
    return rows, total, dict(status_counts), dict(source_counts), dict(risk_counts)


def get_release_or_404(db: Session, release_id: int) -> GovernanceReleaseRequest:
    row = db.query(GovernanceReleaseRequest).filter(GovernanceReleaseRequest.id == release_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="governance_release_not_found")
    return row


def list_release_events(db: Session, release_id: int) -> list[GovernanceReleaseEvent]:
    return (
        db.query(GovernanceReleaseEvent)
        .filter(GovernanceReleaseEvent.release_id == release_id)
        .order_by(GovernanceReleaseEvent.created_at.asc(), GovernanceReleaseEvent.id.asc())
        .all()
    )


def transition_release(
    db: Session,
    row: GovernanceReleaseRequest,
    *,
    action: str,
    payload: GovernanceReleaseAction,
    actor,
) -> GovernanceReleaseRequest:
    if action not in TRANSITIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="governance_release_unknown_action")
    if row.status not in TRANSITIONS[action]:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"governance_release_invalid_transition:{row.status}->{action}")
    if row.status in TERMINAL_STATUSES and action != "rollback":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="governance_release_terminal")

    old = _snapshot(row)
    now = utc_now()
    row.status = ACTION_STATUS[action]
    row.updated_at = now
    if action == "submit":
        row.submitted_at = now
    elif action == "approve":
        row.approved_at = now
        row.approved_by = getattr(actor, "id", None)
    elif action == "publish":
        row.published_at = now
        row.published_by = getattr(actor, "id", None)
    elif action == "rollback":
        row.rolled_back_at = now
        row.rolled_back_by = getattr(actor, "id", None)
    event = _event(
        db,
        row,
        actor_id=getattr(actor, "id", None),
        event_type=action,
        note=payload.note,
        payload={
            "from_status": old["status"],
            "to_status": row.status,
            "rollback_plan_present": bool(row.rollback_plan),
            "request_id": get_current_request_id(),
        },
    )
    _audit(
        db,
        row,
        actor_id=getattr(actor, "id", None),
        action=action,
        old_value=old,
        new_value={**_snapshot(row), "event_id": event.id},
    )
    return row


def assert_release_status_catalog_complete() -> None:
    missing = set(ACTION_STATUS.values()) - RELEASE_STATUSES
    if missing:
        raise RuntimeError(f"governance release status catalog missing: {sorted(missing)}")
