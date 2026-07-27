from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from ..enums import EventType, MessageStatus, TicketStatus
from ..models import (
    AIConfigResource,
    AdminAuditLog,
    Market,
    Team,
    Ticket,
    TicketOutboundMessage,
    User,
)
from ..operator_models import OperatorTask
from ..utils.time import utc_now
from ..voice_models import WebchatVoiceSession
from ..webchat_models import WebchatAITurn, WebchatConversation, WebchatMessage
from .ai_config_service import normalize_resource_key
from .audit_service import log_admin_audit, log_event
from .operator_queue import create_operator_task
from .permissions import (
    CAP_AI_CONFIG_MANAGE,
    CAP_QA_MANAGE,
    has_global_case_visibility,
    resolve_capabilities,
)
from .tenant_query_authority import (
    ActorTenantQueryScope,
    TenantQueryScopeError,
    actor_tenant_query_scope,
)

TERMINAL_TASK_STATUSES = (
    "resolved",
    "dropped",
    "replayed",
    "replay_failed",
    "cancelled",
)
QA_SAMPLE_LIMIT = 12
QA_RESOURCE_PREFIX = "qa_knowledge_gap"


class QATrainingError(RuntimeError):
    pass


def _value(raw: Any) -> Any:
    return raw.value if hasattr(raw, "value") else raw


def _json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clip(value: Any, limit: int) -> str | None:
    text = " ".join(str(value or "").strip().split())
    return text[:limit] if text else None


def _actor_scope(db: Session, user: User) -> ActorTenantQueryScope:
    try:
        return actor_tenant_query_scope(
            db,
            user,
            require_bound_tenant=True,
        )
    except TenantQueryScopeError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc


def _require_qa_capability(db: Session, user: User) -> set[str]:
    capabilities = resolve_capabilities(user, db)
    if not ({CAP_QA_MANAGE, CAP_AI_CONFIG_MANAGE} & capabilities):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="qa_training_requires_capability",
        )
    return capabilities


def _visible_ticket_query(
    db: Session,
    user: User,
    scope: ActorTenantQueryScope,
):
    query = scope.tickets(db)
    if not has_global_case_visibility(user, db):
        query = query.filter(
            or_(
                Ticket.team_id == user.team_id,
                Ticket.assignee_id == user.id,
            )
        )
    return query


def _visible_ticket_ids(
    db: Session,
    user: User,
    scope: ActorTenantQueryScope,
):
    return _visible_ticket_query(db, user, scope).with_entities(Ticket.id)


def _visible_task_query(
    db: Session,
    user: User,
    scope: ActorTenantQueryScope,
):
    query = scope.operator_tasks(db)
    if has_global_case_visibility(user, db):
        return query
    visible_ids = _visible_ticket_ids(db, user, scope)
    return query.filter(
        or_(
            OperatorTask.assignee_id == user.id,
            OperatorTask.ticket_id.in_(visible_ids),
        )
    )


def _score_tone(score: int) -> str:
    if score < 65:
        return "danger"
    if score < 80:
        return "warning"
    return "success"


def _sample(
    *,
    sample_key: str,
    source_type: str,
    ticket: Ticket,
    score: int,
    summary: str,
    created_at,
    transcript: str | None = None,
) -> dict[str, Any]:
    return {
        "sample_key": sample_key,
        "source_type": source_type,
        "ticket_id": ticket.id,
        "ticket_no": ticket.ticket_no,
        "title": ticket.issue_summary or ticket.title,
        "status": _value(ticket.status),
        "assignee_name": (
            ticket.assignee.display_name if ticket.assignee is not None else None
        ),
        "score": score,
        "risk": _score_tone(score),
        "summary": summary,
        "transcript": _clip(transcript, 1000),
        "created_at": created_at.isoformat() if created_at else None,
        "href": "/workspace",
        "appeal_allowed": True,
        "reviewed": False,
    }


def _voice_samples(
    db: Session,
    user: User,
    scope: ActorTenantQueryScope,
) -> list[dict[str, Any]]:
    rows = (
        db.query(WebchatVoiceSession, Ticket)
        .join(Ticket, Ticket.id == WebchatVoiceSession.ticket_id)
        .filter(
            Ticket.id.in_(_visible_ticket_ids(db, user, scope)),
            WebchatVoiceSession.mode != "internal_ai_demo",
            WebchatVoiceSession.ended_at.is_not(None),
        )
        .options(joinedload(Ticket.assignee))
        .order_by(WebchatVoiceSession.ended_at.desc(), WebchatVoiceSession.id.desc())
        .limit(QA_SAMPLE_LIMIT)
        .all()
    )
    result: list[dict[str, Any]] = []
    for session, ticket in rows:
        duration = int(session.duration_seconds or 0)
        score = 92 if session.end_reason in {"completed", "customer_left"} else 68
        if duration and duration < 20:
            score = min(score, 72)
        result.append(
            _sample(
                sample_key=f"voice:{session.id}",
                source_type="voice",
                ticket=ticket,
                score=score,
                summary=(
                    f"Voice session {session.status}; duration {duration}s; "
                    f"end reason {session.end_reason or 'unknown'}."
                ),
                transcript=None,
                created_at=session.ended_at or session.created_at,
            )
        )
    return result


def _webchat_samples(
    db: Session,
    user: User,
    scope: ActorTenantQueryScope,
) -> list[dict[str, Any]]:
    rows = (
        db.query(WebchatMessage, Ticket, WebchatAITurn)
        .join(Ticket, Ticket.id == WebchatMessage.ticket_id)
        .outerjoin(WebchatAITurn, WebchatAITurn.id == WebchatMessage.ai_turn_id)
        .filter(
            Ticket.id.in_(_visible_ticket_ids(db, user, scope)),
            WebchatMessage.direction.in_(("agent", "ai")),
        )
        .options(joinedload(Ticket.assignee))
        .order_by(WebchatMessage.created_at.desc(), WebchatMessage.id.desc())
        .limit(QA_SAMPLE_LIMIT)
        .all()
    )
    result: list[dict[str, Any]] = []
    for message, ticket, turn in rows:
        score = 90
        if turn is not None and turn.status in {"failed", "timeout"}:
            score = 58
        elif message.safety_level not in {None, "safe", "passed"}:
            score = 64
        result.append(
            _sample(
                sample_key=f"webchat:{message.id}",
                source_type="webchat",
                ticket=ticket,
                score=score,
                summary=(
                    f"WebChat {_value(message.direction)} response; "
                    f"delivery {message.delivery_status or 'unknown'}."
                ),
                transcript=message.body,
                created_at=message.created_at,
            )
        )
    return result


def _email_samples(
    db: Session,
    user: User,
    scope: ActorTenantQueryScope,
) -> list[dict[str, Any]]:
    rows = (
        db.query(TicketOutboundMessage, Ticket)
        .join(Ticket, Ticket.id == TicketOutboundMessage.ticket_id)
        .filter(
            Ticket.id.in_(_visible_ticket_ids(db, user, scope)),
            TicketOutboundMessage.channel == "email",
        )
        .options(joinedload(Ticket.assignee))
        .order_by(
            TicketOutboundMessage.created_at.desc(),
            TicketOutboundMessage.id.desc(),
        )
        .limit(QA_SAMPLE_LIMIT)
        .all()
    )
    result: list[dict[str, Any]] = []
    for outbound, ticket in rows:
        status_value = _value(outbound.status)
        score = 92 if status_value == MessageStatus.sent.value else 55
        result.append(
            _sample(
                sample_key=f"email:{outbound.id}",
                source_type="email",
                ticket=ticket,
                score=score,
                summary=f"Email outbound state {status_value}.",
                transcript=outbound.body,
                created_at=outbound.created_at,
            )
        )
    return result


def _ticket_samples(
    db: Session,
    user: User,
    scope: ActorTenantQueryScope,
) -> list[dict[str, Any]]:
    rows = (
        _visible_ticket_query(db, user, scope)
        .options(joinedload(Ticket.assignee))
        .order_by(Ticket.updated_at.desc(), Ticket.id.desc())
        .limit(QA_SAMPLE_LIMIT)
        .all()
    )
    result: list[dict[str, Any]] = []
    for ticket in rows:
        complete = bool(ticket.issue_summary and ticket.customer_request)
        terminal = ticket.status in {
            TicketStatus.resolved,
            TicketStatus.closed,
            TicketStatus.canceled,
        }
        score = 90 if complete else 66
        if terminal and not ticket.resolution_summary:
            score = min(score, 52)
        result.append(
            _sample(
                sample_key=f"ticket:{ticket.id}",
                source_type="ticket",
                ticket=ticket,
                score=score,
                summary=(
                    "Case context and closure evidence are complete."
                    if score >= 80
                    else "Case requires context or closure-evidence review."
                ),
                transcript=ticket.last_customer_message or ticket.customer_request,
                created_at=ticket.updated_at,
            )
        )
    return result


def _training_tasks(
    db: Session,
    user: User,
    scope: ActorTenantQueryScope,
) -> list[dict[str, Any]]:
    rows = (
        _visible_task_query(db, user, scope)
        .filter(
            OperatorTask.task_type.in_(
                ("agent_appeal", "knowledge_gap", "qa_review")
            ),
            OperatorTask.status.notin_(TERMINAL_TASK_STATUSES),
        )
        .order_by(OperatorTask.priority.asc(), OperatorTask.created_at.asc())
        .limit(100)
        .all()
    )
    return [
        {
            "task_id": row.id,
            "task_type": row.task_type,
            "status": row.status,
            "priority": row.priority,
            "ticket_id": row.ticket_id,
            "assignee_id": row.assignee_id,
            "reason_code": row.reason_code,
            "payload": _json(row.payload_json),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "href": "/workspace" if row.ticket_id else "/qa-training",
        }
        for row in rows
    ]


def _active_appeals(
    db: Session,
    user: User,
    scope: ActorTenantQueryScope,
) -> list[dict[str, Any]]:
    return [
        task
        for task in _training_tasks(db, user, scope)
        if task["task_type"] == "agent_appeal"
    ]


def _knowledge_gap_resources(
    db: Session,
    scope: ActorTenantQueryScope,
) -> list[dict[str, Any]]:
    rows = (
        db.query(AIConfigResource)
        .filter(
            AIConfigResource.market_id.in_(scope.active_market_ids()),
            AIConfigResource.is_active.is_(True),
            AIConfigResource.config_type == "knowledge",
            AIConfigResource.resource_key.like(f"{QA_RESOURCE_PREFIX}:%"),
        )
        .order_by(AIConfigResource.updated_at.desc(), AIConfigResource.id.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "resource_id": row.id,
            "resource_key": row.resource_key,
            "title": row.title,
            "description": row.description,
            "market_id": row.market_id,
            "scope_type": row.scope_type,
            "draft_version": row.draft_version,
            "published_version": row.published_version,
            "status": "published" if row.published_version else "draft",
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "href": "/knowledge",
        }
        for row in rows
    ]


def _training_modules() -> list[dict[str, Any]]:
    return [
        {
            "key": "policy-grounding",
            "title": "Policy grounding",
            "status": "available",
            "next": "Review policy citation and unsupported-claim samples.",
        },
        {
            "key": "case-closure",
            "title": "Case closure quality",
            "status": "available",
            "next": "Review closure evidence and customer outcome records.",
        },
        {
            "key": "handoff-ownership",
            "title": "Handoff and ownership",
            "status": "available",
            "next": "Review responsibility transfer and customer recontact.",
        },
        {
            "key": "channel-writing",
            "title": "Channel writing",
            "status": "available",
            "next": "Compare WebChat, Email and Voice response contracts.",
        },
    ]


def _qa_market_id(
    db: Session,
    user: User,
    scope: ActorTenantQueryScope,
) -> int:
    if user.team_id is not None:
        team = (
            scope.teams(db)
            .filter(Team.id == user.team_id, Team.is_active.is_(True))
            .first()
        )
        if team is not None and team.market_id is not None:
            market = (
                scope.markets(db)
                .filter(
                    Market.id == team.market_id,
                    Market.is_active.is_(True),
                )
                .first()
            )
            if market is not None:
                return int(market.id)
    markets = (
        scope.markets(db)
        .filter(Market.is_active.is_(True))
        .order_by(Market.id.asc())
        .limit(2)
        .all()
    )
    if len(markets) != 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="qa_training_market_scope_required",
        )
    return int(markets[0].id)


def _knowledge_gap_resource_key(
    scope: ActorTenantQueryScope,
    gap_key: str,
    title: str,
) -> str:
    raw = f"{scope.tenant_key}:{gap_key}:{title}".strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")[:80]
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return normalize_resource_key(f"{QA_RESOURCE_PREFIX}:{slug}:{digest}")


def build_qa_training(db: Session, current_user: User) -> dict[str, Any]:
    capabilities = _require_qa_capability(db, current_user)
    scope = _actor_scope(db, current_user)
    samples = (
        _voice_samples(db, current_user, scope)
        + _webchat_samples(db, current_user, scope)
        + _email_samples(db, current_user, scope)
        + _ticket_samples(db, current_user, scope)
    )
    samples.sort(
        key=lambda item: (
            item.get("risk") != "danger",
            item.get("score", 100),
            item.get("sample_key", ""),
        )
    )
    samples = samples[:40]
    tasks = _training_tasks(db, current_user, scope)
    appeals = [task for task in tasks if task["task_type"] == "agent_appeal"]
    gaps = _knowledge_gap_resources(db, scope)
    tenant_user_ids = select(User.id).where(scope.model_predicate(User))
    reviewed_7d = int(
        db.query(func.count(AdminAuditLog.id))
        .filter(
            AdminAuditLog.actor_id.in_(tenant_user_ids),
            AdminAuditLog.action.like("qa_training.%"),
            AdminAuditLog.created_at >= utc_now() - timedelta(days=7),
        )
        .scalar()
        or 0
    )
    return {
        "generated_at": utc_now().isoformat(),
        "role": _value(current_user.role),
        "user_id": current_user.id,
        "capabilities": sorted(capabilities),
        "metrics": [
            {
                "key": "qa_backlog",
                "label": "QA review backlog",
                "value": sum(1 for item in samples if item["score"] < 80),
                "tone": "warning",
            },
            {
                "key": "training_backlog",
                "label": "Training tasks",
                "value": len(tasks),
                "tone": "warning" if tasks else "success",
            },
            {
                "key": "appeals",
                "label": "Active appeals",
                "value": len(appeals),
                "tone": "warning" if appeals else "success",
            },
            {
                "key": "knowledge_gaps",
                "label": "Knowledge gaps",
                "value": len(gaps),
                "tone": "warning" if gaps else "success",
            },
            {
                "key": "reviewed_7d",
                "label": "Reviews in 7 days",
                "value": reviewed_7d,
                "tone": "default",
            },
        ],
        "qa_samples": samples,
        "training_tasks": tasks,
        "appeals": appeals,
        "knowledge_gaps": gaps,
        "training_modules": _training_modules(),
        "facts": {
            "tenant_id": scope.tenant_id,
            "tenant_key": scope.tenant_key,
            "sample_count": len(samples),
            "training_task_count": len(tasks),
            "appeal_count": len(appeals),
            "knowledge_gap_count": len(gaps),
            "reviewed_7d": reviewed_7d,
        },
    }


def _visible_ticket(
    db: Session,
    user: User,
    scope: ActorTenantQueryScope,
    ticket_id: int,
) -> Ticket:
    ticket = (
        _visible_ticket_query(db, user, scope)
        .filter(Ticket.id == ticket_id)
        .first()
    )
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found",
        )
    return ticket


def submit_agent_appeal(
    db: Session,
    current_user: User,
    payload,
) -> dict[str, Any]:
    _require_qa_capability(db, current_user)
    scope = _actor_scope(db, current_user)
    ticket = _visible_ticket(db, current_user, scope, int(payload.ticket_id))
    sample_key = _clip(payload.sample_key, 160) or f"ticket:{ticket.id}"
    reason = _clip(payload.reason, 1000) or "QA appeal"
    appeal_key = f"{sample_key}:user:{current_user.id}"[:160]
    task, created = create_operator_task(
        db,
        tenant_id=scope.tenant_id,
        source_type="qa",
        task_type="agent_appeal",
        source_id=appeal_key,
        ticket_id=ticket.id,
        reason_code="agent_appeal",
        priority=30,
        payload={
            "sample_key": sample_key,
            "reason": reason,
            "submitted_by": current_user.id,
            "submitted_at": utc_now().isoformat(),
        },
    )
    task.assignee_id = current_user.id
    task.updated_at = utc_now()
    log_event(
        db,
        ticket_id=ticket.id,
        actor_id=current_user.id,
        event_type=EventType.comment_added,
        field_name="qa.appeal",
        note="QA appeal submitted",
        payload={
            "task_id": task.id,
            "sample_key": sample_key,
            "created": created,
        },
    )
    log_admin_audit(
        db,
        actor_id=current_user.id,
        action="qa_training.appeal.submitted",
        target_type="operator_task",
        target_id=task.id,
        new_value={
            "tenant_id": scope.tenant_id,
            "ticket_id": ticket.id,
            "sample_key": sample_key,
            "created": created,
        },
    )
    db.flush()
    return {
        "ok": True,
        "task_id": task.id,
        "created": created,
        "status": task.status,
        "appeal_key": appeal_key,
        "submitted_at": task.updated_at,
    }


def submit_knowledge_gap(
    db: Session,
    current_user: User,
    payload,
) -> dict[str, Any]:
    _require_qa_capability(db, current_user)
    scope = _actor_scope(db, current_user)
    ticket = None
    if payload.ticket_id is not None:
        ticket = _visible_ticket(
            db,
            current_user,
            scope,
            int(payload.ticket_id),
        )
    gap_key = _clip(payload.gap_key, 160) or "qa-gap"
    title = _clip(payload.title, 255) or "QA knowledge gap"
    description = _clip(payload.description, 2000)
    market_id = _qa_market_id(db, current_user, scope)
    resource_key = _knowledge_gap_resource_key(scope, gap_key, title)
    row = (
        db.query(AIConfigResource)
        .filter(
            AIConfigResource.market_id == market_id,
            AIConfigResource.resource_key == resource_key,
        )
        .first()
    )
    created = row is None
    content = json.dumps(
        {
            "schema": "nexus.qa-knowledge-gap.v2",
            "tenant_id": scope.tenant_id,
            "tenant_key": scope.tenant_key,
            "market_id": market_id,
            "gap_key": gap_key,
            "title": title,
            "description": description,
            "ticket_id": ticket.id if ticket is not None else None,
            "submitted_by": current_user.id,
            "submitted_at": utc_now().isoformat(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    if row is None:
        row = AIConfigResource(
            config_type="knowledge",
            resource_key=resource_key,
            title=title,
            description=description,
            scope_type="market",
            market_id=market_id,
            draft_content=content,
            published_content=None,
            draft_version=1,
            published_version=0,
            is_active=True,
            created_by=current_user.id,
            updated_by=current_user.id,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(row)
        db.flush()
    else:
        row.title = title
        row.description = description
        row.draft_content = content
        row.draft_version = int(row.draft_version or 0) + 1
        row.updated_by = current_user.id
        row.updated_at = utc_now()
        db.flush()

    task, task_created = create_operator_task(
        db,
        tenant_id=scope.tenant_id,
        source_type="qa",
        task_type="knowledge_gap",
        source_id=gap_key,
        ticket_id=ticket.id if ticket is not None else None,
        reason_code="knowledge_gap",
        priority=40,
        payload={
            "gap_key": gap_key,
            "resource_id": row.id,
            "resource_key": row.resource_key,
            "market_id": market_id,
            "title": title,
            "ticket_id": ticket.id if ticket is not None else None,
        },
    )
    log_admin_audit(
        db,
        actor_id=current_user.id,
        action="qa_training.knowledge_gap.submitted",
        target_type="ai_config_resource",
        target_id=row.id,
        new_value={
            "tenant_id": scope.tenant_id,
            "market_id": market_id,
            "resource_key": row.resource_key,
            "ticket_id": ticket.id if ticket is not None else None,
            "task_id": task.id,
            "created": created,
            "task_created": task_created,
        },
    )
    db.flush()
    return {
        "ok": True,
        "resource_id": row.id,
        "resource_key": row.resource_key,
        "task_id": task.id,
        "created": created,
        "task_created": task_created,
        "status": "draft",
        "submitted_at": row.updated_at,
    }


__all__ = [
    "build_qa_training",
    "submit_agent_appeal",
    "submit_knowledge_gap",
]
