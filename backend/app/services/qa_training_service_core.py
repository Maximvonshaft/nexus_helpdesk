from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from ..enums import ConversationState, EventType, MessageStatus, SourceChannel, TicketStatus
from ..models import (
    AIConfigResource,
    AdminAuditLog,
    Customer,
    Ticket,
    TicketEvent,
    TicketOutboundMessage,
    User,
)
from ..operator_models import OperatorTask
from ..utils.time import ensure_utc, utc_now
from ..voice_models import WebchatVoiceSession
from ..webchat_models import WebchatAITurn, WebchatMessage
from .ai_config_service import normalize_resource_key
from .audit_service import log_admin_audit, log_event
from .operator_queue import create_operator_task
from .permissions import (
    CAP_AI_CONFIG_MANAGE,
    CAP_QA_MANAGE,
    CAP_TICKET_READ,
    has_global_case_visibility,
    resolve_capabilities,
)

ACTIVE_TICKET_STATUSES = (
    TicketStatus.new,
    TicketStatus.pending_assignment,
    TicketStatus.in_progress,
    TicketStatus.waiting_internal,
    TicketStatus.escalated,
)
TERMINAL_TASK_STATUSES = ("resolved", "dropped", "replayed", "replay_failed", "cancelled")
TRAINING_TASK_TYPES = ("training", "coaching", "qa_feedback", "knowledge_gap", "qa_appeal")
KNOWLEDGE_CONFIG_TYPES = ("knowledge", "policy", "sop", "rules")
HEALTHY_VOICE_TRANSCRIPT_STATUSES = ("ready", "completed", "done", "redacted")
LOW_AI_CONFIDENCE = 0.75


def _value(raw: Any) -> Any:
    return raw.value if hasattr(raw, "value") else raw


def _visible_ticket_query(db: Session, user: User):
    query = db.query(Ticket)
    if has_global_case_visibility(user, db):
        return query
    predicates = [Ticket.assignee_id == user.id]
    if user.team_id is not None:
        predicates.append(Ticket.team_id == user.team_id)
    return query.filter(or_(*predicates))


def _active_tickets(query):
    return query.filter(Ticket.status.in_(ACTIVE_TICKET_STATUSES))


def _with_ticket_visibility(query, user: User):
    if has_global_case_visibility(user, query.session):
        return query
    predicates = [Ticket.assignee_id == user.id]
    if user.team_id is not None:
        predicates.append(Ticket.team_id == user.team_id)
    return query.filter(or_(*predicates))


def _count(query, column) -> int:
    return int(query.with_entities(func.count(column)).scalar() or 0)


def _tone(value: int, *, danger: int, warning: int = 1) -> str:
    if value >= danger:
        return "danger"
    if value >= warning:
        return "warning"
    return "success"


def _score_tone(score: int) -> str:
    if score < 70:
        return "danger"
    if score < 86:
        return "warning"
    return "success"


def _sample_score(base: int, risks: list[str]) -> int:
    return max(0, min(100, base - len([risk for risk in risks if risk]) * 12))


def _short(value: str | None, *, fallback: str = "-", limit: int = 120) -> str:
    text = (value or "").strip()
    if not text:
        return fallback
    return text if len(text) <= limit else f"{text[:limit - 1]}..."


def _customer_name(ticket: Ticket) -> str | None:
    customer = getattr(ticket, "customer", None)
    if isinstance(customer, Customer):
        return customer.name
    return None


def _agent_name(ticket: Ticket) -> str | None:
    assignee = getattr(ticket, "assignee", None)
    if isinstance(assignee, User):
        return assignee.display_name
    return None


def _sample(
    *,
    key: str,
    channel: str,
    sample: str,
    ticket: Ticket,
    score: int,
    risks: list[str],
    feedback: str,
    source: str,
    created_at,
    agent_appeal: str = "allowed within 48h",
) -> dict[str, Any]:
    return {
        "key": key,
        "channel": channel,
        "sample": sample,
        "ticket_id": ticket.id,
        "ticket_no": ticket.ticket_no,
        "customer_name": _customer_name(ticket),
        "agent_name": _agent_name(ticket),
        "ai_pre_score": score,
        "risk": " · ".join(risks) if risks else "none",
        "feedback": feedback,
        "agent_appeal": agent_appeal,
        "appeal_status": "not_applicable" if agent_appeal == "n/a" else "available",
        "appeal_task_id": None,
        "source": source,
        "created_at": ensure_utc(created_at).isoformat() if created_at else None,
        "href": "/workspace",
        "evidence": risks or ["eligible QA sample"],
    }


def _active_appeals(db: Session, user: User) -> dict[str, OperatorTask]:
    rows = (
        _with_ticket_visibility(
            db.query(OperatorTask, Ticket)
            .outerjoin(Ticket, Ticket.id == OperatorTask.ticket_id)
            .filter(
                OperatorTask.task_type == "qa_appeal",
                OperatorTask.status.notin_(TERMINAL_TASK_STATUSES),
            ),
            user,
        )
        .order_by(OperatorTask.updated_at.desc(), OperatorTask.id.desc())
        .all()
    )
    appeals: dict[str, OperatorTask] = {}
    for task, _ticket in rows:
        if task.source_id:
            appeals.setdefault(task.source_id, task)
    return appeals


def _active_knowledge_gap_tasks(db: Session, user: User) -> dict[str, OperatorTask]:
    rows = (
        _with_ticket_visibility(
            db.query(OperatorTask, Ticket)
            .outerjoin(Ticket, Ticket.id == OperatorTask.ticket_id)
            .filter(
                OperatorTask.task_type == "knowledge_gap",
                OperatorTask.status.notin_(TERMINAL_TASK_STATUSES),
            ),
            user,
        )
        .order_by(OperatorTask.updated_at.desc(), OperatorTask.id.desc())
        .all()
    )
    tasks: dict[str, OperatorTask] = {}
    for task, _ticket in rows:
        if task.source_id:
            tasks.setdefault(task.source_id, task)
    return tasks


def _task_payload(task: OperatorTask | None) -> dict[str, Any]:
    if not task or not task.payload_json:
        return {}
    try:
        payload = json.loads(task.payload_json)
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _voice_samples(db: Session, user: User) -> list[dict[str, Any]]:
    query = (
        db.query(WebchatVoiceSession, Ticket)
        .join(Ticket, Ticket.id == WebchatVoiceSession.ticket_id)
        .options(joinedload(Ticket.customer), joinedload(Ticket.assignee))
        .filter(WebchatVoiceSession.mode != "internal_ai_demo")
    )
    rows = (
        _with_ticket_visibility(query, user)
        .order_by(WebchatVoiceSession.updated_at.desc(), WebchatVoiceSession.id.desc())
        .limit(12)
        .all()
    )
    samples: list[dict[str, Any]] = []
    for session, ticket in rows:
        risks: list[str] = []
        if session.recording_status in {"notice_required", "consent_required"}:
            risks.append(f"recording {session.recording_status}")
        if session.transcript_status not in HEALTHY_VOICE_TRANSCRIPT_STATUSES:
            risks.append(f"transcript {session.transcript_status}")
        if session.summary_status in {"pending", "failed"}:
            risks.append(f"summary {session.summary_status}")
        if session.ai_handoff_reason:
            risks.append(_short(session.ai_handoff_reason, limit=80))
        if session.ended_at is None and session.status in {"active", "accepted", "ringing"}:
            risks.append("wrap-up pending")
        if not risks and session.status not in {"ended", "completed"}:
            continue
        score = _sample_score(94, risks)
        samples.append(
            _sample(
                key=f"webcall:{session.public_id}",
                channel="WebCall",
                sample=f"Call {session.public_id}",
                ticket=ticket,
                score=score,
                risks=risks,
                feedback="coach verification script and require wrap-up evidence" if risks else "use as golden call example",
                source="webchat_voice_sessions",
                created_at=session.updated_at,
            )
        )
    return samples


def _webchat_samples(db: Session, user: User) -> list[dict[str, Any]]:
    review_tickets = (
        _active_tickets(_visible_ticket_query(db, user))
        .options(joinedload(Ticket.customer), joinedload(Ticket.assignee))
        .filter(
            Ticket.source_channel == SourceChannel.web_chat,
            Ticket.conversation_state == ConversationState.human_review_required,
        )
        .order_by(Ticket.updated_at.desc(), Ticket.id.desc())
        .limit(8)
        .all()
    )
    samples = [
        _sample(
            key=f"webchat-ticket:{ticket.id}",
            channel="WebChat",
            sample=ticket.ticket_no,
            ticket=ticket,
            score=_sample_score(
                86,
                [
                    "human review required",
                    _short(ticket.missing_fields, fallback="missing evidence"),
                ],
            ),
            risks=[
                "human review required",
                _short(ticket.missing_fields, fallback="missing evidence"),
            ],
            feedback="create knowledge gap or require policy citation before customer reply",
            source="tickets.conversation_state",
            created_at=ticket.updated_at,
        )
        for ticket in review_tickets
    ]

    ai_turn_rows = (
        _with_ticket_visibility(
            db.query(WebchatAITurn, Ticket)
            .join(Ticket, Ticket.id == WebchatAITurn.ticket_id)
            .options(joinedload(Ticket.customer), joinedload(Ticket.assignee))
            .filter(
                or_(
                    WebchatAITurn.status.in_(("failed", "timeout", "cancelled")),
                    WebchatAITurn.fallback_reason.is_not(None),
                    WebchatAITurn.fact_gate_reason.is_not(None),
                    WebchatAITurn.is_public_reply_allowed.is_(False),
                )
            ),
            user,
        )
        .order_by(WebchatAITurn.updated_at.desc(), WebchatAITurn.id.desc())
        .limit(8)
        .all()
    )
    for turn, ticket in ai_turn_rows:
        risks = [
            _short(turn.status_reason, fallback="AI turn needs review"),
            _short(turn.fact_gate_reason, fallback="", limit=80),
            _short(turn.fallback_reason, fallback="", limit=80),
        ]
        risks = [risk for risk in risks if risk]
        samples.append(
            _sample(
                key=f"webchat-ai-turn:{turn.id}",
                channel="WebChat",
                sample=f"AI turn {turn.id}",
                ticket=ticket,
                score=_sample_score(82, risks),
                risks=risks,
                feedback="review answer quality and add missing knowledge or reply macro",
                source="webchat_ai_turns",
                created_at=turn.updated_at,
            )
        )
    return samples


def _email_samples(db: Session, user: User) -> list[dict[str, Any]]:
    rows = (
        _with_ticket_visibility(
            db.query(TicketOutboundMessage, Ticket)
            .join(Ticket, Ticket.id == TicketOutboundMessage.ticket_id)
            .options(joinedload(Ticket.customer), joinedload(Ticket.assignee))
            .filter(TicketOutboundMessage.channel == SourceChannel.email),
            user,
        )
        .order_by(TicketOutboundMessage.updated_at.desc(), TicketOutboundMessage.id.desc())
        .limit(12)
        .all()
    )
    samples: list[dict[str, Any]] = []
    for message, ticket in rows:
        risks: list[str] = []
        if message.status in {MessageStatus.dead, MessageStatus.failed}:
            risks.append(
                _short(
                    message.failure_code or message.provider_status,
                    fallback=_value(message.status),
                    limit=80,
                )
            )
        if message.status == MessageStatus.draft:
            risks.append("draft pending QA-sensitive customer reply")
        if message.mailbox_thread_id is None:
            risks.append("mailbox thread evidence missing")
        if not risks and message.status != MessageStatus.sent:
            risks.append(_value(message.status))
        score = _sample_score(96 if message.status == MessageStatus.sent else 88, risks)
        samples.append(
            _sample(
                key=f"email:{message.id}",
                channel="Email",
                sample=message.subject or f"Outbound #{message.id}",
                ticket=ticket,
                score=score,
                risks=risks,
                feedback="review delivery status, subject clarity and timeline evidence" if risks else "use as golden email example",
                source="ticket_outbound_messages",
                created_at=message.updated_at,
                agent_appeal="n/a" if not risks else "allowed within 48h",
            )
        )
    return samples


def _ticket_quality_samples(db: Session, user: User) -> list[dict[str, Any]]:
    rows = (
        _active_tickets(_visible_ticket_query(db, user))
        .options(joinedload(Ticket.customer), joinedload(Ticket.assignee))
        .filter(
            or_(
                Ticket.missing_fields.is_not(None),
                Ticket.required_action.is_not(None),
                Ticket.ai_confidence < LOW_AI_CONFIDENCE,
            )
        )
        .order_by(Ticket.updated_at.desc(), Ticket.id.desc())
        .limit(10)
        .all()
    )
    samples = []
    for ticket in rows:
        risks = [
            _short(ticket.missing_fields, fallback="", limit=80),
            _short(ticket.required_action, fallback="", limit=80),
        ]
        if ticket.ai_confidence is not None and ticket.ai_confidence < LOW_AI_CONFIDENCE:
            risks.append(f"low AI confidence {ticket.ai_confidence:.2f}")
        risks = [risk for risk in risks if risk]
        samples.append(
            _sample(
                key=f"ticket-quality:{ticket.id}",
                channel=_value(ticket.source_channel),
                sample=ticket.ticket_no,
                ticket=ticket,
                score=_sample_score(88, risks),
                risks=risks,
                feedback="coach evidence collection before final customer reply",
                source="tickets.ai_quality_fields",
                created_at=ticket.updated_at,
            )
        )
    return samples


def _qa_queue(db: Session, user: User) -> list[dict[str, Any]]:
    rows = (
        _voice_samples(db, user)
        + _webchat_samples(db, user)
        + _email_samples(db, user)
        + _ticket_quality_samples(db, user)
    )
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        deduped.setdefault(row["key"], row)
    appeals = _active_appeals(db, user)
    for row in deduped.values():
        task = appeals.get(row["key"])
        if task is None:
            continue
        row["appeal_status"] = task.status
        row["appeal_task_id"] = task.id
        row["agent_appeal"] = f"appeal {task.status}"
    return sorted(
        deduped.values(),
        key=lambda item: (item["ai_pre_score"], item.get("created_at") or ""),
        reverse=False,
    )[:12]


def _knowledge_gaps(
    db: Session,
    user: User,
    qa_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    active_tasks = _active_knowledge_gap_tasks(db, user)
    rows = (
        db.query(AIConfigResource)
        .filter(
            AIConfigResource.is_active.is_(True),
            AIConfigResource.config_type.in_(KNOWLEDGE_CONFIG_TYPES),
            or_(
                AIConfigResource.published_version == 0,
                AIConfigResource.draft_summary.is_not(None),
            ),
        )
        .order_by(AIConfigResource.updated_at.desc(), AIConfigResource.id.desc())
        .limit(8)
        .all()
    )
    gaps = [
        {
            "key": f"ai-config:{item.id}",
            "title": item.name,
            "source": item.config_type,
            "status": "draft" if item.published_version == 0 else "needs_review",
            "owner": "AI Ops",
            "next": "Run retrieve test, resolve conflicts, then publish or reject",
            "href": "/ai-control",
            "evidence": _short(item.draft_summary or item.description, fallback=item.resource_key),
            "resource_id": item.id,
            "ticket_id": None,
            "sample_key": None,
            "channel": None,
            "sample": None,
        }
        for item in rows
    ]
    for row in qa_rows:
        risk = str(row.get("risk") or "")
        if not any(
            token in risk.lower()
            for token in ("knowledge", "policy", "citation", "missing", "fact", "evidence")
        ):
            continue
        key = f"sample:{row['key']}"
        task = active_tasks.get(key)
        task_payload = _task_payload(task)
        gaps.append(
            {
                "key": key,
                "title": f"{row['channel']} sample needs knowledge closure",
                "source": row["source"],
                "status": task.status if task else "sampled",
                "owner": "AI Ops" if task else "Lead / AI Ops",
                "next": "Review draft knowledge, run golden test, then publish or reject"
                if task
                else "Create or update a knowledge draft from the sampled customer utterance",
                "href": "/ai-control" if task else row["href"],
                "evidence": risk,
                "resource_id": task_payload.get("resource_id") if task else None,
                "ticket_id": row.get("ticket_id"),
                "sample_key": row["key"],
                "channel": row.get("channel"),
                "sample": row.get("sample") or row.get("ticket_no"),
            }
        )
    deduped: dict[str, dict[str, Any]] = {}
    for gap in gaps:
        deduped.setdefault(gap["key"], gap)
    return list(deduped.values())[:10]


def _training_tasks(
    db: Session,
    user: User,
    qa_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = (
        _with_ticket_visibility(
            db.query(OperatorTask, Ticket)
            .outerjoin(Ticket, Ticket.id == OperatorTask.ticket_id)
            .filter(
                OperatorTask.task_type.in_(TRAINING_TASK_TYPES),
                OperatorTask.status.notin_(TERMINAL_TASK_STATUSES),
            ),
            user,
        )
        .order_by(OperatorTask.priority.asc(), OperatorTask.updated_at.desc(), OperatorTask.id.desc())
        .limit(8)
        .all()
    )
    tasks = [
        {
            "key": f"task:{task.id}",
            "title": task.reason_code or f"{task.task_type.replace('_', ' ').title()} task",
            "owner": "Lead" if task.assignee_id is None else f"user:{task.assignee_id}",
            "priority": task.priority,
            "status": task.status,
            "source": task.source_type,
            "next": "Review agent appeal, adjust score if valid, then close with audit note"
            if task.task_type == "qa_appeal"
            else "Score sample, send coaching feedback, then close or escalate",
            "href": "/workspace",
            "enabled": True,
            "capability": CAP_QA_MANAGE,
        }
        for task, _ticket in rows
    ]
    for row in qa_rows[:3]:
        if row["ai_pre_score"] >= 86:
            continue
        tasks.append(
            {
                "key": f"derived:{row['key']}",
                "title": f"Coach {row['agent_name'] or 'assigned agent'} on {row['channel']}",
                "owner": "Lead",
                "priority": max(10, 100 - int(row["ai_pre_score"])),
                "status": "derived",
                "source": row["source"],
                "next": row["feedback"],
                "href": row["href"],
                "enabled": True,
                "capability": CAP_QA_MANAGE,
            }
        )
    return tasks[:10]


def _scorecard(metrics: dict[str, int]) -> list[dict[str, Any]]:
    rows = [
        (
            "identity-check",
            "身份核验",
            96 - metrics["voice_risk"] * 10,
            "WebCall consent/transcript/wrap-up evidence",
            "Coach verification script",
        ),
        (
            "evidence-citation",
            "证据引用",
            94 - metrics["safety_reviews"] * 8,
            "human review, missing evidence and policy citation samples",
            "Create knowledge gap before final reply",
        ),
        (
            "ai-answer-quality",
            "AI 答复质量",
            92 - metrics["ai_failures"] * 10,
            "AI fallback, fact gate and blocked public replies",
            "Feed bad-answer sample into AI Ops",
        ),
        (
            "email-delivery",
            "Email 发送闭环",
            96 - metrics["email_risk"] * 12,
            "dead/failed/draft outbound Email rows",
            "Review delivery and timeline evidence",
        ),
        (
            "timeline-audit",
            "Timeline / Audit",
            min(100, 70 + metrics["recent_audit"] * 2),
            "recent TicketEvent/AdminAuditLog evidence",
            "Keep review feedback auditable",
        ),
    ]
    return [
        {
            "key": key,
            "criterion": label,
            "score": max(0, min(100, score)),
            "tone": _score_tone(max(0, min(100, score))),
            "evidence": evidence,
            "next": next_step,
        }
        for key, label, score, evidence, next_step in rows
    ]


def _loop_steps(
    knowledge_gap_count: int,
    coaching_task_count: int,
    capabilities: set[str],
) -> list[dict[str, Any]]:
    ai_ops_enabled = CAP_AI_CONFIG_MANAGE in capabilities
    return [
        {
            "key": "customer-sample",
            "step": "客户问题",
            "owner": "Agent / System",
            "artifact": "conversation/call/email sample",
            "status": "implemented",
            "href": "/qa-training",
            "enabled": True,
        },
        {
            "key": "gap-detection",
            "step": "标记知识缺口",
            "owner": "Lead",
            "artifact": f"{knowledge_gap_count} current gap candidates",
            "status": "implemented" if knowledge_gap_count else "linked",
            "href": "/qa-training",
            "enabled": True,
        },
        {
            "key": "coaching",
            "step": "Coaching feedback",
            "owner": "Lead",
            "artifact": f"{coaching_task_count} active/derived coaching tasks",
            "status": "implemented" if coaching_task_count else "linked",
            "href": "/qa-training",
            "enabled": True,
        },
        {
            "key": "agent-appeal",
            "step": "Agent appeal",
            "owner": "Lead / Agent",
            "artifact": "qa_appeal operator_tasks with ticket audit",
            "status": "implemented",
            "href": "/qa-training",
            "enabled": True,
        },
        {
            "key": "ai-ops-review",
            "step": "AI Ops 审核",
            "owner": "AI Ops",
            "artifact": "draft knowledge item or reject reason",
            "status": "linked",
            "href": "/ai-control",
            "enabled": ai_ops_enabled,
        },
        {
            "key": "golden-test",
            "step": "黄金测试",
            "owner": "Product / QA",
            "artifact": "retrieve-test expected and forbidden answer",
            "status": "linked",
            "href": "/ai-control",
            "enabled": ai_ops_enabled,
        },
        {
            "key": "publish-monitor",
            "step": "发布与命中监控",
            "owner": "Manager / Admin",
            "artifact": "release version, rollback plan, hit-rate trend",
            "status": "linked",
            "href": "/ai-control",
            "enabled": ai_ops_enabled,
        },
    ]


def _template_blocks() -> list[dict[str, str]]:
    return [
        {
            "key": "qa-queue",
            "label": "QA Queue",
            "backend_contract": "/api/lite/qa-training",
            "status": "implemented",
            "evidence": "samples from WebCall, WebChat, Email and ticket AI fields",
            "href": "/qa-training",
        },
        {
            "key": "scorecard",
            "label": "Scorecard",
            "backend_contract": "computed QA metrics",
            "status": "implemented",
            "evidence": "identity, citation, AI quality, Email delivery and audit criteria",
            "href": "/qa-training",
        },
        {
            "key": "coaching",
            "label": "Coaching Feedback",
            "backend_contract": "operator_tasks + derived QA samples",
            "status": "implemented",
            "evidence": "training/coaching/knowledge_gap tasks or generated follow-up actions",
            "href": "/workspace",
        },
        {
            "key": "knowledge-gap",
            "label": "Knowledge Gap Loop",
            "backend_contract": "POST /api/lite/qa-training/knowledge-gaps + ai_config_resources + operator_tasks(task_type=knowledge_gap)",
            "status": "implemented",
            "evidence": "sample-derived gaps create knowledge drafts, operator tasks and ticket/admin audit",
            "href": "/qa-training",
        },
        {
            "key": "appeal",
            "label": "Agent Appeal",
            "backend_contract": "POST /api/lite/qa-training/appeals + operator_tasks(task_type=qa_appeal)",
            "status": "implemented",
            "evidence": "appeals persist as qa_appeal operator tasks and write ticket/admin audit",
            "href": "/qa-training",
        },
    ]


def _knowledge_gap_resource_key(gap_key: str, title: str) -> str:
    source = re.sub(r"[^a-z0-9]+", "-", f"{gap_key}-{title}".lower()).strip("-")
    return normalize_resource_key(f"qa-gap-{source[:96] or 'draft'}")[:120]


def submit_knowledge_gap(db: Session, current_user: User, payload) -> dict[str, Any]:
    capabilities = resolve_capabilities(current_user, db)
    if CAP_QA_MANAGE not in capabilities:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="qa_training_requires_capability",
        )

    ticket = None
    if payload.ticket_id is not None:
        ticket = (
            _visible_ticket_query(db, current_user)
            .filter(Ticket.id == payload.ticket_id)
            .first()
        )
        if ticket is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="qa_knowledge_gap_ticket_not_found",
            )

    gap_key = str(payload.gap_key).strip()
    evidence = [
        str(item).strip()
        for item in (payload.evidence or [])
        if str(item).strip()
    ][:12]
    summary = payload.summary or (evidence[0] if evidence else payload.title)
    resource_key = _knowledge_gap_resource_key(gap_key, payload.title)
    draft_content = {
        "source": "qa_training",
        "gap_key": gap_key,
        "source_type": payload.source,
        "ticket_id": payload.ticket_id,
        "ticket_no": ticket.ticket_no if ticket else None,
        "channel": payload.channel,
        "sample": payload.sample,
        "evidence": evidence,
        "recommended_action": "draft policy or FAQ answer, run retrieval/golden tests, then publish through AI Control",
        "submitted_by": current_user.id,
        "submitted_at": utc_now().isoformat(),
    }
    row = (
        db.query(AIConfigResource)
        .filter(AIConfigResource.resource_key == resource_key)
        .first()
    )
    created_resource = row is None
    if row is None:
        row = AIConfigResource(
            resource_key=resource_key,
            config_type="knowledge",
            name=payload.title,
            description=f"QA Training knowledge gap from {payload.source or 'sample'}",
            scope_type="global",
            is_active=True,
            published_version=0,
            created_by=current_user.id,
        )
        db.add(row)
        db.flush()

    row.name = payload.title
    row.draft_summary = summary
    row.draft_content_json = draft_content
    row.updated_by = current_user.id
    row.updated_at = utc_now()
    db.flush()

    task_payload = {
        **draft_content,
        "resource_id": row.id,
        "resource_key": row.resource_key,
    }
    task, created_task = create_operator_task(
        db,
        source_type="qa",
        task_type="knowledge_gap",
        source_id=gap_key,
        ticket_id=ticket.id if ticket else None,
        reason_code="qa_knowledge_gap",
        priority=35,
        payload=task_payload,
        note=summary,
    )
    task.updated_at = utc_now()

    if ticket is not None:
        log_event(
            db,
            ticket_id=ticket.id,
            actor_id=current_user.id,
            event_type=EventType.field_updated,
            field_name="qa_knowledge_gap",
            new_value=row.resource_key,
            note=summary,
            payload={
                "task_id": task.id,
                "created_task": created_task,
                "created_resource": created_resource,
                **task_payload,
            },
        )
    log_admin_audit(
        db,
        actor_id=current_user.id,
        action="qa.knowledge_gap.submitted",
        target_type="ai_config_resource",
        target_id=row.id,
        new_value={
            "task_id": task.id,
            "created_task": created_task,
            "created_resource": created_resource,
            **task_payload,
        },
    )
    db.flush()
    return {
        "ok": True,
        "resource_id": row.id,
        "resource_key": row.resource_key,
        "task_id": task.id,
        "created": created_resource,
        "status": task.status,
        "ticket_id": ticket.id if ticket else None,
        "gap_key": gap_key,
        "submitted_at": task.updated_at,
    }


def submit_agent_appeal(db: Session, current_user: User, payload) -> dict[str, Any]:
    capabilities = resolve_capabilities(current_user, db)
    if CAP_QA_MANAGE not in capabilities:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="qa_training_requires_capability",
        )
    ticket = (
        _visible_ticket_query(db, current_user)
        .filter(Ticket.id == payload.ticket_id)
        .first()
    )
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="qa_appeal_ticket_not_found",
        )

    sample_key = str(payload.sample_key).strip()
    appeal_payload = {
        "sample_key": sample_key,
        "ticket_id": ticket.id,
        "ticket_no": ticket.ticket_no,
        "channel": payload.channel,
        "sample": payload.sample,
        "current_score": payload.current_score,
        "requested_score": payload.requested_score,
        "reason": payload.reason,
        "evidence": payload.evidence,
        "submitted_by": current_user.id,
        "submitted_at": utc_now().isoformat(),
    }
    priority = max(10, 100 - int(payload.current_score or 70))
    task, created = create_operator_task(
        db,
        source_type="qa",
        task_type="qa_appeal",
        source_id=sample_key,
        ticket_id=ticket.id,
        reason_code="agent_appeal",
        priority=priority,
        payload=appeal_payload,
        note=payload.reason,
    )
    task.assignee_id = current_user.id
    task.updated_at = utc_now()
    log_event(
        db,
        ticket_id=ticket.id,
        actor_id=current_user.id,
        event_type=EventType.field_updated,
        field_name="qa_agent_appeal",
        new_value=task.status,
        note=payload.reason,
        payload={"task_id": task.id, "created": created, **appeal_payload},
    )
    log_admin_audit(
        db,
        actor_id=current_user.id,
        action="qa.agent_appeal.submitted",
        target_type="operator_task",
        target_id=task.id,
        new_value={"created": created, **appeal_payload},
    )
    db.flush()
    return {
        "ok": True,
        "task_id": task.id,
        "created": created,
        "status": task.status,
        "ticket_id": ticket.id,
        "sample_key": sample_key,
        "appeal_status": task.status,
        "submitted_at": task.updated_at,
    }


def build_qa_training(db: Session, current_user: User) -> dict[str, Any]:
    now = utc_now()
    capabilities = resolve_capabilities(current_user, db)
    if CAP_QA_MANAGE not in capabilities:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="qa_training_requires_capability",
        )

    qa_rows = _qa_queue(db, current_user)
    knowledge_gaps = _knowledge_gaps(db, current_user, qa_rows)
    training_tasks = _training_tasks(db, current_user, qa_rows)
    appeal_count = len(_active_appeals(db, current_user))
    recent_window = now - timedelta(days=7)

    safety_reviews = _count(
        _active_tickets(_visible_ticket_query(db, current_user)).filter(
            Ticket.conversation_state == ConversationState.human_review_required
        ),
        Ticket.id,
    ) + int(
        _with_ticket_visibility(
            db.query(func.count(WebchatMessage.id))
            .join(Ticket, Ticket.id == WebchatMessage.ticket_id)
            .filter(WebchatMessage.safety_level.in_(("review", "block"))),
            current_user,
        ).scalar()
        or 0
    )
    ai_failures = int(
        _with_ticket_visibility(
            db.query(func.count(WebchatAITurn.id))
            .join(Ticket, Ticket.id == WebchatAITurn.ticket_id)
            .filter(
                or_(
                    WebchatAITurn.status.in_(("failed", "timeout", "cancelled")),
                    WebchatAITurn.fallback_reason.is_not(None),
                    WebchatAITurn.fact_gate_reason.is_not(None),
                    WebchatAITurn.is_public_reply_allowed.is_(False),
                )
            ),
            current_user,
        ).scalar()
        or 0
    )
    voice_risk = int(
        _with_ticket_visibility(
            db.query(func.count(WebchatVoiceSession.id))
            .join(Ticket, Ticket.id == WebchatVoiceSession.ticket_id)
            .filter(
                WebchatVoiceSession.mode != "internal_ai_demo",
                or_(
                    WebchatVoiceSession.recording_status.in_(("notice_required", "consent_required")),
                    WebchatVoiceSession.transcript_status.notin_(HEALTHY_VOICE_TRANSCRIPT_STATUSES),
                    WebchatVoiceSession.summary_status.in_(("pending", "failed")),
                    WebchatVoiceSession.ai_handoff_reason.is_not(None),
                ),
            ),
            current_user,
        ).scalar()
        or 0
    )
    email_risk = int(
        _with_ticket_visibility(
            db.query(func.count(TicketOutboundMessage.id))
            .join(Ticket, Ticket.id == TicketOutboundMessage.ticket_id)
            .filter(
                TicketOutboundMessage.channel == SourceChannel.email,
                TicketOutboundMessage.status.in_(
                    (MessageStatus.dead, MessageStatus.failed, MessageStatus.draft)
                ),
            ),
            current_user,
        ).scalar()
        or 0
    )
    recent_audit = int(
        db.query(func.count(AdminAuditLog.id))
        .filter(AdminAuditLog.created_at >= recent_window)
        .scalar()
        or 0
    ) + int(
        _with_ticket_visibility(
            db.query(func.count(TicketEvent.id))
            .join(Ticket, Ticket.id == TicketEvent.ticket_id)
            .filter(TicketEvent.created_at >= recent_window),
            current_user,
        ).scalar()
        or 0
    )
    golden_examples = len(
        [row for row in qa_rows if int(row["ai_pre_score"]) >= 90]
    )
    metrics = {
        "safety_reviews": safety_reviews,
        "ai_failures": ai_failures,
        "voice_risk": voice_risk,
        "email_risk": email_risk,
        "recent_audit": recent_audit,
    }

    return {
        "generated_at": now.isoformat(),
        "role": _value(current_user.role),
        "user_id": current_user.id,
        "capabilities": sorted(capabilities),
        "kpis": [
            {
                "key": "qa_queue",
                "label": "QA 样本队列",
                "value": len(qa_rows),
                "hint": "WebCall/WebChat/Email/Ticket 自动抽样",
                "tone": _tone(len(qa_rows), danger=8, warning=1),
            },
            {
                "key": "safety_reviews",
                "label": "安全复核",
                "value": safety_reviews,
                "hint": "human review + safety gate samples",
                "tone": _tone(safety_reviews, danger=5, warning=1),
            },
            {
                "key": "ai_failures",
                "label": "AI 失败/降级",
                "value": ai_failures,
                "hint": "fallback、fact gate、timeout 或 blocked public reply",
                "tone": _tone(ai_failures, danger=3, warning=1),
            },
            {
                "key": "knowledge_gaps",
                "label": "知识缺口",
                "value": len(knowledge_gaps),
                "hint": "AI config draft + sample-derived gaps",
                "tone": _tone(len(knowledge_gaps), danger=5, warning=1),
            },
            {
                "key": "coaching_tasks",
                "label": "培训任务",
                "value": len(training_tasks),
                "hint": "operator_tasks + QA-derived coaching",
                "tone": _tone(len(training_tasks), danger=6, warning=1),
            },
            {
                "key": "agent_appeals",
                "label": "Agent 申诉",
                "value": appeal_count,
                "hint": "active qa_appeal operator_tasks",
                "tone": _tone(appeal_count, danger=5, warning=1),
            },
            {
                "key": "golden_examples",
                "label": "可复用样本",
                "value": golden_examples,
                "hint": "pre-score >= 90",
                "tone": "success" if golden_examples else "default",
            },
        ],
        "qa_queue": qa_rows,
        "scorecard": _scorecard(metrics),
        "training_tasks": training_tasks,
        "knowledge_gaps": knowledge_gaps,
        "loop_steps": _loop_steps(
            len(knowledge_gaps),
            len(training_tasks),
            capabilities,
        ),
        "template_blocks": _template_blocks(),
        "facts": {
            "active_visible_tickets": _count(
                _active_tickets(_visible_ticket_query(db, current_user)),
                Ticket.id,
            ),
            "safety_reviews": safety_reviews,
            "ai_failures": ai_failures,
            "voice_risk": voice_risk,
            "email_risk": email_risk,
            "recent_audit_7d": recent_audit,
            "qa_manage_capability": CAP_QA_MANAGE in capabilities,
            "ticket_read_capability": CAP_TICKET_READ in capabilities,
            "agent_appeal_write_endpoint": "implemented",
            "knowledge_gap_write_endpoint": "implemented",
        },
    }
