from __future__ import annotations

from datetime import timedelta
from typing import Iterable

from fastapi import APIRouter, Depends
from sqlalchemy import and_, case, func, or_, true
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import MessageStatus, SourceChannel, TicketPriority, TicketStatus, UserRole
from ..models import Ticket, TicketOutboundMessage, User
from ..services.permissions import (
    CAP_OUTBOUND_DRAFT_SAVE,
    CAP_OUTBOUND_SEND,
    CAP_TICKET_READ,
    CAP_WEBCALL_VOICE_QUEUE_VIEW,
    ensure_capability,
    resolve_capabilities,
)
from ..utils.time import ensure_utc, utc_now
from ..voice_models import WebchatVoiceSession
from ..webchat_models import WebchatConversation, WebchatHandoffRequest
from ..workbench_schemas import (
    WorkbenchInteractionState,
    WorkbenchMetric,
    WorkbenchQueueItem,
    WorkbenchSummaryRead,
    WorkbenchTask,
    WorkbenchUser,
)
from .deps import get_current_user

router = APIRouter(prefix="/api/workbench", tags=["workbench"])

OPEN_TICKET_STATUSES = (
    TicketStatus.new,
    TicketStatus.pending_assignment,
    TicketStatus.in_progress,
    TicketStatus.waiting_customer,
    TicketStatus.waiting_internal,
    TicketStatus.escalated,
)
ACTIVE_HANDOFF_STATUSES = ("requested", "accepted")
ACTIVE_VOICE_STATUSES = ("created", "ringing", "active")
SLA_RISK_WINDOW = timedelta(minutes=30)


def _value(value) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def _visible_ticket_filter(user: User):
    if user.role in {UserRole.admin, UserRole.manager, UserRole.auditor}:
        return true()
    if user.team_id:
        return or_(Ticket.assignee_id == user.id, Ticket.team_id == user.team_id)
    return Ticket.assignee_id == user.id


def _open_ticket_filter():
    return Ticket.status.in_(OPEN_TICKET_STATUSES)


def _sla_due_filter(now):
    risk_cutoff = now + SLA_RISK_WINDOW
    first_response_due = and_(
        Ticket.first_response_due_at.isnot(None),
        Ticket.first_response_due_at <= risk_cutoff,
        Ticket.first_response_at.is_(None),
    )
    resolution_due = and_(
        Ticket.resolution_due_at.isnot(None),
        Ticket.resolution_due_at <= risk_cutoff,
        Ticket.resolved_at.is_(None),
    )
    return and_(_open_ticket_filter(), or_(first_response_due, resolution_due))


def _overdue_filter(now):
    first_response_overdue = and_(
        Ticket.first_response_due_at.isnot(None),
        Ticket.first_response_due_at < now,
        Ticket.first_response_at.is_(None),
    )
    resolution_overdue = and_(
        Ticket.resolution_due_at.isnot(None),
        Ticket.resolution_due_at < now,
        Ticket.resolved_at.is_(None),
    )
    return and_(_open_ticket_filter(), or_(first_response_overdue, resolution_overdue))


def _priority_order():
    return case(
        (Ticket.priority == TicketPriority.urgent, 0),
        (Ticket.priority == TicketPriority.high, 1),
        (Ticket.priority == TicketPriority.medium, 2),
        else_=3,
    )


def _due_order():
    return case((Ticket.resolution_due_at.is_(None), 1), else_=0)


def _target_route_for_ticket(ticket: Ticket) -> str:
    channel = _value(ticket.source_channel)
    if channel == SourceChannel.web_chat.value:
        return "/webchat"
    if channel == SourceChannel.email.value:
        return "/email"
    return "/workspace"


def _recommended_ticket_action(ticket: Ticket, *, overdue: bool) -> str:
    channel = _value(ticket.source_channel)
    status = _value(ticket.status)
    if overdue:
        return "先回复客户或升级主管"
    if status in {TicketStatus.new.value, TicketStatus.pending_assignment.value}:
        return "认领或分配负责人"
    if status == TicketStatus.waiting_customer.value:
        return "查看客户最新回复"
    if channel == SourceChannel.web_chat.value:
        return "进入 WebChat 接管会话"
    if channel == SourceChannel.email.value:
        return "打开 Email 草稿/回复"
    return "打开工单处理下一步"


def _ticket_queue_item(ticket: Ticket, *, now, kind: str = "ticket", title: str | None = None, action: str | None = None) -> WorkbenchQueueItem:
    due_values = [value for value in (ensure_utc(ticket.first_response_due_at), ensure_utc(ticket.resolution_due_at)) if value is not None]
    due_at = min(due_values) if due_values else None
    overdue = bool(due_at and due_at < now)
    return WorkbenchQueueItem(
        id=f"{kind}:{ticket.id}",
        kind=kind,
        ticket_id=ticket.id,
        ticket_no=ticket.ticket_no,
        title=title or ticket.title,
        customer_name=ticket.customer.name if ticket.customer else None,
        channel=_value(ticket.source_channel),
        status=_value(ticket.status) or "unknown",
        priority=_value(ticket.priority),
        assignee_name=ticket.assignee.display_name if ticket.assignee else None,
        team_name=ticket.team.name if ticket.team else None,
        due_at=due_at,
        overdue=overdue,
        recommended_action=action or _recommended_ticket_action(ticket, overdue=overdue),
        target_route=_target_route_for_ticket(ticket),
        updated_at=ensure_utc(ticket.updated_at),
    )


def _count(db: Session, *criteria) -> int:
    return int(db.query(func.count(Ticket.id)).filter(*criteria).scalar() or 0)


def _count_joined_messages(db: Session, statuses: Iterable[MessageStatus], visible_filter) -> int:
    return int(
        db.query(func.count(TicketOutboundMessage.id))
        .join(Ticket, Ticket.id == TicketOutboundMessage.ticket_id)
        .filter(visible_filter, TicketOutboundMessage.status.in_(tuple(statuses)))
        .scalar()
        or 0
    )


@router.get("/summary", response_model=WorkbenchSummaryRead)
def get_workbench_summary(
    limit: int = 12,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_capability(current_user, CAP_TICKET_READ, db, message="workbench_summary_requires_ticket_read")
    capabilities = resolve_capabilities(current_user, db)
    now = utc_now()
    safe_limit = max(1, min(limit, 25))
    visible_filter = _visible_ticket_filter(current_user)
    open_filter = and_(visible_filter, _open_ticket_filter())
    sla_filter = and_(visible_filter, _sla_due_filter(now))
    overdue_filter = and_(visible_filter, _overdue_filter(now))

    open_count = _count(db, open_filter)
    my_open_count = _count(db, open_filter, Ticket.assignee_id == current_user.id)
    team_open_count = _count(db, open_filter, Ticket.team_id == current_user.team_id) if current_user.team_id else open_count
    sla_risk_count = _count(db, sla_filter)
    overdue_count = _count(db, overdue_filter)
    waiting_customer_count = _count(db, open_filter, Ticket.status == TicketStatus.waiting_customer)

    handoff_count = int(
        db.query(func.count(WebchatHandoffRequest.id))
        .join(Ticket, Ticket.id == WebchatHandoffRequest.ticket_id)
        .filter(visible_filter, WebchatHandoffRequest.status.in_(ACTIVE_HANDOFF_STATUSES))
        .scalar()
        or 0
    )
    voice_count = 0
    if CAP_WEBCALL_VOICE_QUEUE_VIEW in capabilities:
        voice_count = int(
            db.query(func.count(WebchatVoiceSession.id))
            .join(Ticket, Ticket.id == WebchatVoiceSession.ticket_id)
            .filter(visible_filter, WebchatVoiceSession.status.in_(ACTIVE_VOICE_STATUSES), WebchatVoiceSession.mode != "internal_ai_demo")
            .scalar()
            or 0
        )
    failed_outbound_count = _count_joined_messages(db, (MessageStatus.failed, MessageStatus.dead), visible_filter)
    draft_count = _count_joined_messages(db, (MessageStatus.draft,), visible_filter)

    ticket_rows = (
        db.query(Ticket)
        .filter(open_filter)
        .order_by(
            case((_sla_due_filter(now), 0), else_=1),
            _priority_order(),
            _due_order(),
            Ticket.resolution_due_at.asc(),
            Ticket.updated_at.desc(),
            Ticket.id.desc(),
        )
        .limit(safe_limit)
        .all()
    )
    queue = [_ticket_queue_item(ticket, now=now) for ticket in ticket_rows]

    handoff_rows = (
        db.query(WebchatHandoffRequest, Ticket, WebchatConversation)
        .join(Ticket, Ticket.id == WebchatHandoffRequest.ticket_id)
        .join(WebchatConversation, WebchatConversation.id == WebchatHandoffRequest.conversation_id)
        .filter(visible_filter, WebchatHandoffRequest.status.in_(ACTIVE_HANDOFF_STATUSES))
        .order_by(WebchatHandoffRequest.requested_at.asc(), WebchatHandoffRequest.id.asc())
        .limit(4)
        .all()
    )
    for handoff, ticket, conversation in handoff_rows:
        handoff_requested_at = ensure_utc(handoff.requested_at)
        ticket_resolution_due_at = ensure_utc(ticket.resolution_due_at)
        waiting_seconds = max(0, int((now - handoff_requested_at).total_seconds())) if handoff_requested_at else None
        queue.append(
            WorkbenchQueueItem(
                id=f"webchat_handoff:{handoff.id}",
                kind="webchat_handoff",
                ticket_id=ticket.id,
                ticket_no=ticket.ticket_no,
                title=ticket.title,
                customer_name=conversation.visitor_name or (ticket.customer.name if ticket.customer else None),
                channel=SourceChannel.web_chat.value,
                status=handoff.status,
                priority=_value(ticket.priority),
                assignee_name=ticket.assignee.display_name if ticket.assignee else None,
                team_name=ticket.team.name if ticket.team else None,
                due_at=ticket_resolution_due_at,
                overdue=bool(ticket_resolution_due_at and ticket_resolution_due_at < now),
                waiting_seconds=waiting_seconds,
                recommended_action=handoff.recommended_agent_action or "接管 WebChat handoff",
                target_route="/webchat",
                updated_at=ensure_utc(handoff.updated_at),
            )
        )

    if CAP_WEBCALL_VOICE_QUEUE_VIEW in capabilities:
        voice_rows = (
            db.query(WebchatVoiceSession, Ticket)
            .join(Ticket, Ticket.id == WebchatVoiceSession.ticket_id)
            .filter(visible_filter, WebchatVoiceSession.status.in_(ACTIVE_VOICE_STATUSES), WebchatVoiceSession.mode != "internal_ai_demo")
            .order_by(WebchatVoiceSession.ringing_at.asc(), WebchatVoiceSession.id.asc())
            .limit(4)
            .all()
        )
        for session, ticket in voice_rows:
            since = ensure_utc(session.ringing_at) or ensure_utc(session.created_at)
            ticket_resolution_due_at = ensure_utc(ticket.resolution_due_at)
            waiting_seconds = max(0, int((now - since).total_seconds())) if since else None
            queue.append(
                WorkbenchQueueItem(
                    id=f"webcall:{session.public_id}",
                    kind="webcall",
                    ticket_id=ticket.id,
                    ticket_no=ticket.ticket_no,
                    title=ticket.title,
                    customer_name=ticket.customer.name if ticket.customer else None,
                    channel="webcall",
                    status=session.status,
                    priority=_value(ticket.priority),
                    assignee_name=ticket.assignee.display_name if ticket.assignee else None,
                    team_name=ticket.team.name if ticket.team else None,
                    due_at=ticket_resolution_due_at,
                    overdue=bool(ticket_resolution_due_at and ticket_resolution_due_at < now),
                    waiting_seconds=waiting_seconds,
                    recommended_action="接听 WebCall" if session.status in {"created", "ringing"} else "查看活动通话",
                    target_route="/webcall",
                    updated_at=ensure_utc(session.updated_at),
                )
            )

    queue = sorted(
        queue,
        key=lambda item: (
            0 if item.overdue else 1,
            0 if item.kind in {"webcall", "webchat_handoff"} else 1,
            item.due_at or now + timedelta(days=365),
            item.updated_at or now,
        ),
    )[:safe_limit]

    sla_rows = (
        db.query(Ticket)
        .filter(sla_filter)
        .order_by(_due_order(), Ticket.resolution_due_at.asc(), _priority_order(), Ticket.updated_at.desc())
        .limit(6)
        .all()
    )
    sla_risks = [_ticket_queue_item(ticket, now=now, kind="sla_risk", action="先处理 SLA 风险") for ticket in sla_rows]

    channel_counts = {
        _value(channel) or "unknown": int(count or 0)
        for channel, count in db.query(Ticket.source_channel, func.count(Ticket.id)).filter(open_filter).group_by(Ticket.source_channel).all()
    }

    metrics = [
        WorkbenchMetric(key="open", label="可处理工单", value=open_count, hint="当前账号可见且未关闭", target_route="/workspace"),
        WorkbenchMetric(key="mine", label="我的工单", value=my_open_count, hint="负责人是当前账号", target_route="/workspace"),
        WorkbenchMetric(key="team", label="团队待办", value=team_open_count, hint="当前团队或可见范围", target_route="/workspace"),
        WorkbenchMetric(key="sla", label="SLA 风险", value=sla_risk_count, tone="danger" if sla_risk_count else "success", hint="30 分钟内到期或已逾期", target_route="/workspace"),
        WorkbenchMetric(key="handoff", label="WebChat 接管", value=handoff_count, tone="warning" if handoff_count else "success", hint="AI 转人工或人工接管", target_route="/webchat"),
        WorkbenchMetric(key="voice", label="WebCall 来电", value=voice_count, tone="warning" if voice_count else "success", hint="需要接听或查看的通话", target_route="/webcall"),
    ]

    tasks = [
        WorkbenchTask(id="my-open", title="我的处理中工单", count=my_open_count, severity="processing", source="/api/workbench/summary", next_action="按 SLA 剩余时间排序处理", target_route="/workspace"),
        WorkbenchTask(id="sla-risk", title="30 分钟内 SLA 风险", count=sla_risk_count, severity="danger" if sla_risk_count else "success", source="/api/workbench/summary", next_action="先回复客户或升级组长", target_route="/workspace"),
        WorkbenchTask(id="webchat-handoff", title="等待接管的 WebChat", count=handoff_count, severity="warning" if handoff_count else "success", source="/api/workbench/summary", next_action="接管客户会话或释放 AI", target_route="/webchat"),
        WorkbenchTask(id="webcall-incoming", title="WebCall 来电/通话", count=voice_count, severity="warning" if voice_count else "success", source="/api/workbench/summary", next_action="接听、结束或补充通话记录", target_route="/webcall"),
        WorkbenchTask(id="draft-replies", title="待继续的回复草稿", count=draft_count, severity="warning" if draft_count else "success", source="/api/workbench/summary", next_action="继续编辑并发送客户回复", target_route="/workspace"),
        WorkbenchTask(id="failed-outbound", title="发送失败待处理", count=failed_outbound_count, severity="danger" if failed_outbound_count else "success", source="/api/workbench/summary", next_action="进入工单核对线路与重发", target_route="/workspace"),
    ]
    if CAP_OUTBOUND_DRAFT_SAVE not in capabilities and CAP_OUTBOUND_SEND not in capabilities:
        tasks = [task for task in tasks if task.id not in {"draft-replies", "failed-outbound"}]

    interaction_states = [
        WorkbenchInteractionState(key="webchat", label="WebChat 未闭环", count=channel_counts.get(SourceChannel.web_chat.value, 0), tone="warning", target_route="/webchat"),
        WorkbenchInteractionState(key="webcall", label="WebCall 活动", count=voice_count, tone="warning" if voice_count else "success", target_route="/webcall"),
        WorkbenchInteractionState(key="email", label="Email 待处理", count=channel_counts.get(SourceChannel.email.value, 0), tone="warning", target_route="/email"),
        WorkbenchInteractionState(key="waiting-customer", label="等待客户", count=waiting_customer_count, target_route="/workspace"),
        WorkbenchInteractionState(key="sla", label="SLA 风险", count=sla_risk_count, tone="danger" if sla_risk_count else "success", target_route="/workspace"),
        WorkbenchInteractionState(key="failed-send", label="发送失败", count=failed_outbound_count, tone="danger" if failed_outbound_count else "success", target_route="/workspace"),
        WorkbenchInteractionState(key="overdue", label="已逾期", count=overdue_count, tone="danger" if overdue_count else "success", target_route="/workspace"),
    ]

    return WorkbenchSummaryRead(
        generated_at=now,
        user=WorkbenchUser(
            id=current_user.id,
            username=current_user.username,
            display_name=current_user.display_name,
            role=_value(current_user.role) or "agent",
            team_id=current_user.team_id,
            capabilities=sorted(capabilities),
        ),
        metrics=metrics,
        tasks=tasks,
        queue=queue,
        sla_risks=sla_risks,
        interaction_states=interaction_states,
        data_sources=[
            "/api/workbench/summary",
            "tickets",
            "ticket_outbound_messages",
            "webchat_handoff_requests",
            "webchat_voice_sessions",
        ],
    )
