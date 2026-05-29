from __future__ import annotations

import json
from datetime import timedelta
from typing import Iterable

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..enums import ConversationState, EventType, TicketPriority, TicketStatus
from ..models import AdminAuditLog, QAReview, QATrainingTask, Ticket, TicketEvent, TicketOutboundMessage, User
from ..schemas_qa_training import QAQueueRead, QAQueueSummary, QAReviewCreate, QAReviewRead, QASampleRead, QATrainingTaskRead
from ..utils.time import utc_now
from .audit_service import log_admin_audit, log_event


TERMINAL_STATUSES = {TicketStatus.resolved, TicketStatus.closed, TicketStatus.canceled}


def _enum_value(value) -> str:
    return getattr(value, "value", str(value))


def _risk_list_from_json(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _sample_ref(ticket: Ticket) -> str | None:
    return ticket.ticket_no or ticket.source_chat_id or ticket.preferred_reply_contact or f"ticket-{ticket.id}"


def _ticket_risks(ticket: Ticket, outbound_count: int) -> list[str]:
    risks: list[str] = []
    if ticket.first_response_breached or ticket.resolution_breached:
        risks.append("sla_breach")
    if ticket.ai_confidence is not None and ticket.ai_confidence < 0.65:
        risks.append("low_ai_confidence")
    if ticket.missing_fields:
        risks.append("missing_customer_evidence")
    if ticket.status in TERMINAL_STATUSES and not ticket.resolution_summary:
        risks.append("missing_resolution_summary")
    if ticket.assignee_id is None:
        risks.append("agent_owner_missing")
    if ticket.preferred_reply_channel and outbound_count == 0:
        risks.append("reply_not_evidenced")
    if ticket.source_channel and _enum_value(ticket.source_channel) == "web_chat" and ticket.conversation_state == ConversationState.human_review_required:
        risks.append("handoff_review_required")
    if ticket.priority == TicketPriority.urgent:
        risks.append("urgent_case_sampling")
    return risks


def _ai_pre_score(ticket: Ticket, risks: Iterable[str]) -> int:
    risk_penalty = len(list(risks)) * 9
    confidence_penalty = 0
    if ticket.ai_confidence is not None:
        if ticket.ai_confidence < 0.45:
            confidence_penalty = 15
        elif ticket.ai_confidence < 0.65:
            confidence_penalty = 8
    priority_penalty = 6 if ticket.priority == TicketPriority.urgent else 3 if ticket.priority == TicketPriority.high else 0
    return max(35, min(100, 100 - risk_penalty - confidence_penalty - priority_penalty))


def _review_to_read(review: QAReview, training_task: QATrainingTask | None = None) -> QAReviewRead:
    return QAReviewRead(
        id=review.id,
        ticket_id=review.ticket_id,
        sample_channel=review.sample_channel,
        sample_ref=review.sample_ref,
        reviewer_id=review.reviewer_id,
        agent_id=review.agent_id,
        status=review.status,
        ai_pre_score=review.ai_pre_score,
        final_score=review.final_score,
        risks=_risk_list_from_json(review.risks_json),
        feedback=review.feedback,
        knowledge_gap_summary=review.knowledge_gap_summary,
        appeal_status=review.appeal_status,
        training_task=QATrainingTaskRead.model_validate(training_task) if training_task is not None else None,
        created_at=review.created_at,
        updated_at=review.updated_at,
    )


def _latest_reviews_by_ticket(db: Session, ticket_ids: list[int]) -> dict[int, QAReview]:
    if not ticket_ids:
        return {}
    rows = (
        db.query(QAReview)
        .filter(QAReview.ticket_id.in_(ticket_ids))
        .order_by(QAReview.ticket_id.asc(), QAReview.created_at.desc(), QAReview.id.desc())
        .all()
    )
    latest: dict[int, QAReview] = {}
    for row in rows:
        latest.setdefault(row.ticket_id, row)
    return latest


def _outbound_counts_by_ticket(db: Session, ticket_ids: list[int]) -> dict[int, int]:
    if not ticket_ids:
        return {}
    rows = (
        db.query(TicketOutboundMessage.ticket_id, func.count(TicketOutboundMessage.id))
        .filter(TicketOutboundMessage.ticket_id.in_(ticket_ids))
        .group_by(TicketOutboundMessage.ticket_id)
        .all()
    )
    return {int(ticket_id): int(count) for ticket_id, count in rows}


def list_qa_queue(db: Session, *, channel: str | None = None, status_filter: str | None = None, limit: int = 50) -> QAQueueRead:
    normalized_channel = (channel or "").strip()
    normalized_status = (status_filter or "").strip()
    capped_limit = min(max(limit, 1), 100)
    query = db.query(Ticket).order_by(Ticket.updated_at.desc(), Ticket.id.desc())
    if normalized_channel and normalized_channel != "all":
        query = query.filter(Ticket.source_channel == normalized_channel)
    tickets = query.limit(capped_limit * 2).all()
    ticket_ids = [ticket.id for ticket in tickets]
    latest_reviews = _latest_reviews_by_ticket(db, ticket_ids)
    outbound_counts = _outbound_counts_by_ticket(db, ticket_ids)

    samples: list[QASampleRead] = []
    for ticket in tickets:
        review = latest_reviews.get(ticket.id)
        risks = _risk_list_from_json(review.risks_json) if review else _ticket_risks(ticket, outbound_counts.get(ticket.id, 0))
        ai_score = review.ai_pre_score if review else _ai_pre_score(ticket, risks)
        sample_status = review.status if review else ("needs_review" if risks else "ready")
        if normalized_status and normalized_status != "all" and sample_status != normalized_status:
            continue
        samples.append(
            QASampleRead(
                ticket_id=ticket.id,
                ticket_no=ticket.ticket_no,
                title=ticket.title,
                sample_channel=_enum_value(ticket.source_channel),
                sample_ref=review.sample_ref if review else _sample_ref(ticket),
                customer_name=ticket.customer.name if ticket.customer else None,
                agent_id=ticket.assignee_id,
                agent_name=ticket.assignee.display_name if ticket.assignee else None,
                status=sample_status,
                priority=_enum_value(ticket.priority),
                ai_pre_score=ai_score,
                risks=risks,
                feedback=review.feedback if review else None,
                appeal_status=review.appeal_status if review else "not_started",
                knowledge_gap_summary=review.knowledge_gap_summary if review else None,
                updated_at=ticket.updated_at,
                reviewed_at=review.created_at if review else None,
            )
        )
        if len(samples) >= capped_limit:
            break

    reviewed = sum(1 for sample in samples if sample.status == "reviewed")
    needs_review = sum(1 for sample in samples if sample.status == "needs_review")
    average = round(sum(sample.ai_pre_score for sample in samples) / len(samples)) if samples else 0
    open_training_tasks = db.query(QATrainingTask).filter(QATrainingTask.status == "open").count()
    knowledge_gap_tasks = db.query(QATrainingTask).filter(QATrainingTask.status == "open", QATrainingTask.task_type == "knowledge_gap").count()
    return QAQueueRead(
        samples=samples,
        summary=QAQueueSummary(
            total_samples=len(samples),
            needs_review=needs_review,
            reviewed=reviewed,
            average_ai_pre_score=average,
            open_training_tasks=open_training_tasks,
            knowledge_gap_tasks=knowledge_gap_tasks,
        ),
    )


def list_training_tasks(db: Session, *, status_filter: str | None = None, limit: int = 50) -> list[QATrainingTaskRead]:
    normalized_status = (status_filter or "").strip()
    query = db.query(QATrainingTask).order_by(QATrainingTask.created_at.desc(), QATrainingTask.id.desc())
    if normalized_status and normalized_status != "all":
        query = query.filter(QATrainingTask.status == normalized_status)
    rows = query.limit(min(max(limit, 1), 100)).all()
    return [QATrainingTaskRead.model_validate(row) for row in rows]


def create_qa_review(db: Session, payload: QAReviewCreate, *, reviewer: User) -> QAReviewRead:
    ticket = db.query(Ticket).filter(Ticket.id == payload.ticket_id).first()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    outbound_count = db.query(TicketOutboundMessage).filter(TicketOutboundMessage.ticket_id == ticket.id).count()
    inferred_risks = _ticket_risks(ticket, outbound_count)
    risks = payload.risks or inferred_risks
    review = QAReview(
        ticket_id=ticket.id,
        sample_channel=_enum_value(ticket.source_channel),
        sample_ref=_sample_ref(ticket),
        reviewer_id=reviewer.id,
        agent_id=ticket.assignee_id,
        status="reviewed",
        ai_pre_score=_ai_pre_score(ticket, inferred_risks),
        final_score=payload.final_score,
        risks_json=json.dumps(risks, ensure_ascii=False),
        feedback=payload.feedback,
        knowledge_gap_summary=payload.knowledge_gap_summary,
        appeal_status=payload.appeal_status or "not_started",
    )
    db.add(review)
    db.flush()

    task: QATrainingTask | None = None
    if payload.create_training_task:
        task_type = "knowledge_gap" if payload.knowledge_gap_summary else "coaching"
        summary = payload.coaching_summary or payload.feedback[:280]
        task = QATrainingTask(
            review_id=review.id,
            ticket_id=ticket.id,
            agent_id=ticket.assignee_id,
            owner_id=reviewer.id,
            task_type=task_type,
            status="open",
            summary=summary,
            knowledge_gap_summary=payload.knowledge_gap_summary,
            due_at=utc_now() + timedelta(days=7),
            created_by=reviewer.id,
        )
        db.add(task)
        db.flush()

    audit_payload = {
        "ticket_id": ticket.id,
        "sample_channel": review.sample_channel,
        "final_score": payload.final_score,
        "risk_count": len(risks),
        "training_task_id": task.id if task else None,
        "knowledge_gap": bool(payload.knowledge_gap_summary),
    }
    log_event(
        db,
        ticket_id=ticket.id,
        actor_id=reviewer.id,
        event_type=EventType.field_updated,
        field_name="qa_review",
        new_value=json.dumps(audit_payload, ensure_ascii=False),
        note="QA review created",
        payload=audit_payload,
    )
    log_admin_audit(
        db,
        actor_id=reviewer.id,
        action="qa.review.create",
        target_type="qa_review",
        target_id=review.id,
        new_value=audit_payload,
    )
    return _review_to_read(review, task)


def count_qa_audit_evidence(db: Session, *, ticket_id: int) -> dict[str, int]:
    return {
        "reviews": db.query(QAReview).filter(QAReview.ticket_id == ticket_id).count(),
        "training_tasks": db.query(QATrainingTask).filter(QATrainingTask.ticket_id == ticket_id).count(),
        "ticket_events": db.query(TicketEvent).filter(TicketEvent.ticket_id == ticket_id, TicketEvent.field_name == "qa_review").count(),
        "admin_audits": db.query(AdminAuditLog).filter(AdminAuditLog.action == "qa.review.create").count(),
    }
