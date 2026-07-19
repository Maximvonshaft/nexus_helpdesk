from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from ..enums import ConversationState, JobStatus, MessageStatus, SourceChannel, TicketStatus
from ..models import BackgroundJob, Ticket, TicketOutboundMessage, User
from ..operator_models import OperatorTask
from ..utils.time import ensure_utc, utc_now
from .permissions import (
    CAP_OUTBOUND_DRAFT_SAVE,
    CAP_OUTBOUND_SEND,
    CAP_RUNTIME_MANAGE,
    CAP_TICKET_ASSIGN,
    CAP_TICKET_READ,
    CAP_WEBCALL_VOICE_QUEUE_VIEW,
    CAP_WEBCHAT_HANDOFF_ACCEPT,
    resolve_capabilities,
)
from .scope_permissions import has_global_case_visibility
from .ticket_sla_policy import sla_risk_filter

ACTIVE_TICKET_STATUSES = (
    TicketStatus.new,
    TicketStatus.pending_assignment,
    TicketStatus.in_progress,
    TicketStatus.waiting_internal,
    TicketStatus.escalated,
)
TERMINAL_TASK_STATUSES = ("resolved", "dropped", "replayed", "replay_failed", "cancelled")
EMAIL_MARKERS = ("email", "mail", "smtp", "imap", "pop3")


def _value(raw: Any) -> Any:
    return raw.value if hasattr(raw, "value") else raw


def _visible_ticket_query(db: Session, user: User):
    query = db.query(Ticket)
    if not has_global_case_visibility(user, db):
        query = query.filter(or_(Ticket.team_id == user.team_id, Ticket.assignee_id == user.id))
    return query


def _active_tickets(query):
    return query.filter(Ticket.status.in_(ACTIVE_TICKET_STATUSES))


def _count(query) -> int:
    return int(query.with_entities(func.count(Ticket.id)).scalar() or 0)


def _email_case_filter():
    predicates = [Ticket.source_channel == SourceChannel.email]
    for marker in EMAIL_MARKERS:
        like = f"%{marker}%"
        predicates.extend([Ticket.category.ilike(like), Ticket.sub_category.ilike(like), Ticket.preferred_reply_channel.ilike(like)])
    return or_(*predicates)


def _active_handoff_count(db: Session, user: User) -> int:
    query = db.query(OperatorTask).outerjoin(Ticket, OperatorTask.ticket_id == Ticket.id)
    if not has_global_case_visibility(user, db):
        query = query.filter(or_(Ticket.team_id == user.team_id, Ticket.assignee_id == user.id, OperatorTask.assignee_id == user.id))
    return int(
        query.filter(
            OperatorTask.task_type == "handoff",
            OperatorTask.status.notin_(TERMINAL_TASK_STATUSES),
        ).with_entities(func.count(OperatorTask.id)).scalar()
        or 0
    )


def _dead_runtime_count(db: Session) -> int:
    jobs = int(db.query(func.count(BackgroundJob.id)).filter(BackgroundJob.status == JobStatus.dead).scalar() or 0)
    outbound = int(db.query(func.count(TicketOutboundMessage.id)).filter(TicketOutboundMessage.status == MessageStatus.dead).scalar() or 0)
    return jobs + outbound


def _tone(count: int, *, danger: int, warning: int = 1) -> str:
    if count >= danger:
        return "danger"
    if count >= warning:
        return "warning"
    return "success"


def _task(key: str, title: str, count: int | str, severity: str, source: str, next_step: str, target: str, href: str, *, enabled: bool = True) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "count": count,
        "severity": severity,
        "source": source,
        "next": next_step,
        "target": target,
        "href": href,
        "enabled": enabled,
    }


def _metric(key: str, label: str, value: int, hint: str, tone: str = "default") -> dict[str, Any]:
    return {"key": key, "label": label, "value": value, "hint": hint, "tone": tone}


def _sla_due_at(ticket: Ticket) -> datetime | None:
    due_values = [ensure_utc(value) for value in (ticket.first_response_due_at, ticket.resolution_due_at) if value is not None]
    return min(due_values) if due_values else None


def _minutes_to_due(ticket: Ticket, now: datetime) -> int | None:
    due_at = _sla_due_at(ticket)
    if due_at is None:
        return None
    return int((due_at - ensure_utc(now)).total_seconds() // 60)


def _sla_priority_rows(db: Session, user: User, now: datetime) -> list[dict[str, Any]]:
    rows = (
        _active_tickets(_visible_ticket_query(db, user))
        .options(joinedload(Ticket.customer), joinedload(Ticket.assignee), joinedload(Ticket.team))
        .filter(or_(Ticket.first_response_due_at.is_not(None), Ticket.resolution_due_at.is_not(None), Ticket.first_response_breached.is_(True), Ticket.resolution_breached.is_(True)))
        .limit(80)
        .all()
    )
    rows.sort(key=lambda ticket: (_minutes_to_due(ticket, now) is None, _minutes_to_due(ticket, now) or 0, ticket.id))
    return [
        {
            "ticket_id": ticket.id,
            "ticket_no": ticket.ticket_no,
            "title": ticket.issue_summary or ticket.title,
            "priority": _value(ticket.priority),
            "status": _value(ticket.status),
            "source_channel": _value(ticket.source_channel),
            "customer_name": ticket.customer.name if ticket.customer else None,
            "assignee_name": ticket.assignee.display_name if ticket.assignee else None,
            "team_name": ticket.team.name if ticket.team else None,
            "resolution_due_at": ticket.resolution_due_at.isoformat() if ticket.resolution_due_at else None,
            "first_response_due_at": ticket.first_response_due_at.isoformat() if ticket.first_response_due_at else None,
            "minutes_to_due": _minutes_to_due(ticket, now),
            "overdue": bool(ticket.first_response_breached or ticket.resolution_breached or ((_sla_due_at(ticket) or now) < now)),
            "href": "/workspace",
        }
        for ticket in rows[:6]
    ]


def _interaction_states() -> list[dict[str, str]]:
    return [
        {"key": "loading", "state": "loading", "user_copy": "正在加载今日任务；超过 8 秒显示重试入口。", "required": "skeleton + retry + request id", "status": "implemented"},
        {"key": "empty", "state": "empty", "user_copy": "当前没有待处理任务，提供下一步入口而不是空表。", "required": "empty state + next best action", "status": "implemented"},
        {"key": "error", "state": "error", "user_copy": "展示失败原因、重试动作和诊断编号。", "required": "error summary + retry", "status": "implemented"},
        {"key": "permission", "state": "permission denied", "user_copy": "只展示当前 capability 可执行的入口。", "required": "capability filtered command", "status": "implemented"},
        {"key": "dirty", "state": "unsaved changes", "user_copy": "编辑类工作台保留离开确认。", "required": "dirty form guard", "status": "implemented"},
    ]


def _command(key: str, label: str, role: str, target: str, href: str, next_step: str, *, enabled: bool, capability: str) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "role": role,
        "target": target,
        "href": href,
        "next": next_step,
        "enabled": enabled,
        "capability": capability,
    }


def build_today_workbench(db: Session, current_user: User) -> dict[str, Any]:
    now = utc_now()
    capabilities = resolve_capabilities(current_user, db)
    visible = _visible_ticket_query(db, current_user)
    active = _active_tickets(visible)

    active_count = _count(active)
    my_tickets = _count(_active_tickets(visible).filter(Ticket.assignee_id == current_user.id))
    unassigned = _count(_active_tickets(visible).filter(Ticket.assignee_id.is_(None)))
    sla_risk = _count(_active_tickets(visible).filter(sla_risk_filter(now)))
    overdue = _count(
        _active_tickets(visible).filter(
            or_(
                Ticket.first_response_breached.is_(True),
                Ticket.resolution_breached.is_(True),
                Ticket.first_response_due_at < now,
                Ticket.resolution_due_at < now,
            )
        )
    )
    ready_to_reply = _count(
        _active_tickets(visible).filter(
            or_(
                Ticket.status == TicketStatus.waiting_internal,
                Ticket.conversation_state.in_((ConversationState.human_review_required, ConversationState.ready_to_reply, ConversationState.reopened_by_customer)),
            )
        )
    )
    email_cases = _count(_active_tickets(visible).filter(_email_case_filter()))
    webchat_cases = _count(_active_tickets(visible).filter(Ticket.source_channel == SourceChannel.web_chat))
    handoff_count = _active_handoff_count(db, current_user) if CAP_WEBCHAT_HANDOFF_ACCEPT in capabilities else 0
    runtime_recovery = _dead_runtime_count(db) if CAP_RUNTIME_MANAGE in capabilities else 0

    tasks = [
        _task("webchat-handoff", "待人工接入 WebChat", handoff_count, _tone(handoff_count, danger=5), "/api/webchat/admin/handoff/queue", "先接入等待最久且 AI 已暂停的会话", "workspace", "/workspace", enabled=CAP_WEBCHAT_HANDOFF_ACCEPT in capabilities),
        _task("my-tickets", "我的处理中工单", my_tickets, _tone(my_tickets, danger=12), "/api/lite/cases?assignee_id=me", "按 SLA 剩余时间排序处理", "workspace", "/workspace", enabled=CAP_TICKET_READ in capabilities),
        _task("sla-risk", "30 分钟内 SLA 风险", sla_risk, _tone(sla_risk, danger=3), "/api/lite/today-workbench", "先回复客户或升级", "workspace", "/workspace", enabled=CAP_TICKET_READ in capabilities),
        _task("customer-ready", "客户待回复", ready_to_reply, _tone(ready_to_reply, danger=8), "/api/lite/cases?status=pending_human", "避免客户二次催问", "workspace", "/workspace", enabled=CAP_TICKET_READ in capabilities),
        _task("email-waiting", "等待中的 Email", email_cases, _tone(email_cases, danger=8), "/api/lite/cases?channel=email", "进入渠道与案例工作台处理邮件", "channels", "/channels", enabled=bool({CAP_OUTBOUND_DRAFT_SAVE, CAP_OUTBOUND_SEND} & capabilities)),
    ]
    if CAP_TICKET_ASSIGN in capabilities:
        tasks.append(_task("unassigned", "未分配队列", unassigned, _tone(unassigned, danger=10), "/api/lite/cases?assignee_id=null", "按语言、市场和负载分配", "workspace", "/workspace", enabled=True))
    if CAP_RUNTIME_MANAGE in capabilities:
        tasks.append(_task("runtime-recovery", "运行异常待恢复", runtime_recovery, _tone(runtime_recovery, danger=5), "/api/admin/queue/summary", "进入运行与审计页面修复", "runtime", "/runtime", enabled=True))

    return {
        "generated_at": now.isoformat(),
        "role": _value(current_user.role),
        "user_id": current_user.id,
        "capabilities": sorted(capabilities),
        "tasks": tasks,
        "metrics": [
            _metric("active_tickets", "可见活动工单", active_count, "当前账号授权范围内未关闭工单"),
            _metric("webchat_cases", "WebChat 队列", webchat_cases, "网站会话与人工接管来源工单", _tone(webchat_cases, danger=20)),
            _metric("email_cases", "Email 队列", email_cases, "邮件来源或邮件候选工单", _tone(email_cases, danger=20)),
            _metric("sla_risk", "SLA 风险", sla_risk, "30 分钟内到期或已违约", _tone(sla_risk, danger=3)),
            _metric("overdue", "已超时", overdue, "first response 或 resolution 已违约", _tone(overdue, danger=1)),
            _metric("ready_to_reply", "客户待回复", ready_to_reply, "需要人工回复或复核的会话", _tone(ready_to_reply, danger=8)),
        ],
        "sla_priorities": _sla_priority_rows(db, current_user, now),
        "interaction_states": _interaction_states(),
        "command_center": [
            _command("cmd-webchat", "接入等待最久的 WebChat", "operator", "workspace", "/workspace", "打开统一工作台处理接管", enabled=CAP_WEBCHAT_HANDOFF_ACCEPT in capabilities, capability=CAP_WEBCHAT_HANDOFF_ACCEPT),
            _command("cmd-ticket", "处理临近 SLA 工单", "operator", "workspace", "/workspace", "打开统一工作台处理 SLA 风险", enabled=CAP_TICKET_READ in capabilities, capability=CAP_TICKET_READ),
            _command("cmd-email", "处理等待中的 Email", "operator", "channels", "/channels", "查看渠道状态并进入相关案例", enabled=bool({CAP_OUTBOUND_DRAFT_SAVE, CAP_OUTBOUND_SEND} & capabilities), capability=CAP_OUTBOUND_SEND),
            _command("cmd-webcall", "查看 WebCall 队列", "operator", "workspace", "/workspace", "在统一队列处理来电案例", enabled=CAP_WEBCALL_VOICE_QUEUE_VIEW in capabilities, capability=CAP_WEBCALL_VOICE_QUEUE_VIEW),
            _command("cmd-runtime", "运行恢复 / dead 重排", "runtime_manager", "runtime", "/runtime", "打开运行与审计页面", enabled=CAP_RUNTIME_MANAGE in capabilities, capability=CAP_RUNTIME_MANAGE),
        ],
    }
