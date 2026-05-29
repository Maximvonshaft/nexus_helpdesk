from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, joinedload

from ..enums import ConversationState, SourceChannel, TicketPriority, TicketStatus, UserRole
from ..models import Ticket, User
from ..utils.time import ensure_utc, utc_now
from ..webchat_models import WebchatHandoffRequest
from .permissions import (
    CAP_CHANNEL_ACCOUNT_MANAGE,
    CAP_OUTBOUND_DRAFT_SAVE,
    CAP_OUTBOUND_SEND,
    CAP_RUNTIME_MANAGE,
    CAP_TICKET_ASSIGN,
    CAP_TICKET_READ,
    CAP_USER_MANAGE,
    CAP_WEBCALL_VOICE_ACCEPT,
    CAP_WEBCALL_VOICE_END,
    CAP_WEBCALL_VOICE_QUEUE_VIEW,
    CAP_WEBCALL_VOICE_READ,
    ensure_capability,
    resolve_capabilities,
)

TERMINAL_STATUSES = (TicketStatus.resolved, TicketStatus.closed, TicketStatus.canceled)
ACTIVE_STATUSES = tuple(status for status in TicketStatus if status not in TERMINAL_STATUSES)
PENDING_HUMAN_STATUSES = (
    TicketStatus.new,
    TicketStatus.pending_assignment,
    TicketStatus.waiting_internal,
    TicketStatus.escalated,
)
WEBCHAT_ATTENTION_STATES = (
    ConversationState.human_review_required,
    ConversationState.human_owned,
    ConversationState.ready_to_reply,
    ConversationState.reopened_by_customer,
)

ROLE_LABELS = {
    UserRole.agent: "一线客服",
    UserRole.lead: "组长",
    UserRole.manager: "客服主管",
    UserRole.admin: "管理员",
    UserRole.auditor: "审计员",
}

ROLE_MISSIONS = {
    UserRole.agent: "先处理自己的 WebChat handoff、等待中的 Email 和临近 SLA 工单，再补齐草稿、发送与 timeline 证据。",
    UserRole.lead: "先消化未分配队列和溢出的 handoff，再处理升级单、WebCall 支援与质量抽检。",
    UserRole.manager: "先看 SLA、跨渠道风险与运行恢复，再确认责任人、兜底线路和审计证据。",
    UserRole.admin: "先确认运行恢复、RBAC、渠道健康和高风险变更，再处理系统级阻塞。",
    UserRole.auditor: "先复核权限变更、时间线证据和发送留痕，再抽查高风险客户交互。",
}


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _visible_ticket_filters(current_user: User) -> list[Any]:
    if current_user.role in {UserRole.admin, UserRole.manager, UserRole.auditor}:
        return []
    filters: list[Any] = [Ticket.assignee_id == current_user.id]
    if current_user.team_id is not None:
        filters.append(Ticket.team_id == current_user.team_id)
    return [or_(*filters)]


def _visible_tickets(db: Session, current_user: User):
    query = db.query(Ticket)
    for condition in _visible_ticket_filters(current_user):
        query = query.filter(condition)
    return query


def _active_visible_tickets(db: Session, current_user: User):
    return _visible_tickets(db, current_user).filter(Ticket.status.in_(ACTIVE_STATUSES))


def _count(query) -> int:
    return int(query.order_by(None).count())


def _sla_risk_filter(window_end):
    return and_(
        Ticket.status.in_(ACTIVE_STATUSES),
        Ticket.sla_paused.is_(False),
        or_(
            and_(Ticket.first_response_at.is_(None), Ticket.first_response_due_at.is_not(None), Ticket.first_response_due_at <= window_end),
            and_(Ticket.resolution_due_at.is_not(None), Ticket.resolution_due_at <= window_end),
        ),
    )


def _task(
    *,
    key: str,
    title: str,
    count: int,
    severity: str,
    source: str,
    next_step: str,
    target_route: str = "/workspace",
    target_filter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "count": count,
        "severity": severity,
        "source": source,
        "next": next_step,
        "target_route": target_route,
        "target_filter": target_filter or {},
    }


def _entrypoint(key: str, label: str, route: str, hint: str, source: str) -> dict[str, str]:
    return {"key": key, "label": label, "route": route, "hint": hint, "source": source}


def _command(key: str, label: str, route: str, source: str, audit: str) -> dict[str, str]:
    return {"key": key, "label": label, "route": route, "source": source, "audit": audit}


def _visible_entrypoints(capabilities: set[str]) -> list[dict[str, str]]:
    entrypoints = [
        _entrypoint("workspace", "处理工单", "/workspace", "分配、回复、证据与时间线", "/api/lite/cases"),
        _entrypoint("webchat", "WebChat", "/webchat", "handoff、AI 暂停和人工回复", "/api/webchat/admin/handoff/queue"),
    ]
    if {CAP_WEBCALL_VOICE_READ, CAP_WEBCALL_VOICE_QUEUE_VIEW}.issubset(capabilities):
        entrypoints.append(
            _entrypoint("webcall", "WebCall", "/webcall", "来电队列、身份验证、AI 建议和会话动作", "/api/webcall/operator/workbench")
        )
    if {CAP_OUTBOUND_DRAFT_SAVE, CAP_OUTBOUND_SEND} & capabilities:
        entrypoints.append(
            _entrypoint("email", "Email", "/email", "邮件队列、草稿保存、外发和 timeline 回写", "/api/tickets/{ticket_id}/outbound/*")
        )
    if CAP_RUNTIME_MANAGE in capabilities:
        entrypoints.append(_entrypoint("runtime", "运行恢复", "/runtime", "dead/requeue 与同步健康", "/api/admin/queues/summary"))
    if CAP_CHANNEL_ACCOUNT_MANAGE in capabilities:
        entrypoints.append(_entrypoint("accounts", "发送线路", "/accounts", "渠道账号和兜底线路", "/api/admin/channel-accounts"))
    if CAP_USER_MANAGE in capabilities:
        entrypoints.append(_entrypoint("users", "账号权限", "/users", "RBAC 与权限变更审计", "/api/admin/capabilities/catalog"))
    return entrypoints


def _interaction_states() -> list[dict[str, str]]:
    return [
        {
            "state": "loading",
            "operator_signal": "显示骨架与刷新状态",
            "product_rule": "不得误报 0；保留上次可用上下文",
            "source": "React Query loading/error state",
        },
        {
            "state": "empty",
            "operator_signal": "明确当前没有待办",
            "product_rule": "展示下一步入口，不让客服停在空页",
            "source": "/api/workbench/today",
        },
        {
            "state": "error",
            "operator_signal": "显示可重试失败",
            "product_rule": "保留模块边界，不吞掉接口错误",
            "source": "统一 API client ApiError",
        },
        {
            "state": "permission denied",
            "operator_signal": "入口仍解释权限语义",
            "product_rule": "RBAC 决定可见范围与动作能力",
            "source": "routeAccess + backend capabilities",
        },
        {
            "state": "unsaved changes",
            "operator_signal": "草稿/动作必须显式保存",
            "product_rule": "Email draft/save 与 timeline 写回分离验证",
            "source": "/api/tickets/{ticket_id}/outbound/draft",
        },
    ]


def _command_center(capabilities: set[str]) -> list[dict[str, str]]:
    commands = [
        _command("cmd-ticket", "打开工单处理", "/workspace", "/api/lite/cases", "TicketEvent + timeline"),
        _command("cmd-webchat", "接入等待最久的 WebChat", "/webchat", "/api/webchat/admin/handoff/queue", "WebchatEvent + handoff decision"),
        _command("cmd-trace", "查看 timeline/audit", "/workspace", "/api/tickets/{ticket_id}/timeline", "Ticket timeline"),
    ]
    if {CAP_OUTBOUND_DRAFT_SAVE, CAP_OUTBOUND_SEND} & capabilities:
        commands.append(
            _command("cmd-email", "处理等待中的 Email", "/email", "/api/tickets/{ticket_id}/outbound/draft|send", "TicketOutboundMessage + TicketEvent")
        )
    if {CAP_WEBCALL_VOICE_ACCEPT, CAP_WEBCALL_VOICE_END, CAP_WEBCALL_VOICE_QUEUE_VIEW} & capabilities:
        commands.append(
            _command("cmd-webcall", "打开 WebCall 工作台", "/webcall", "/api/webcall/operator/workbench", "Voice session + handoff + ticket timeline")
        )
    if CAP_RUNTIME_MANAGE in capabilities:
        commands.append(_command("cmd-runtime", "进入运行恢复", "/runtime", "/api/admin/queues/summary", "AdminAuditLog + queue recovery result"))
    if CAP_USER_MANAGE in capabilities:
        commands.append(_command("cmd-rbac", "复核 RBAC", "/users", "/api/admin/capabilities/catalog", "AdminAuditLog"))
    return commands


def _source_contracts() -> list[str]:
    return [
        "/api/auth/me",
        "/api/workbench/today",
        "/api/lite/cases",
        "/api/webchat/admin/handoff/queue",
        "/api/webchat/admin/conversations",
        "/api/webcall/operator/workbench",
        "/api/tickets/{ticket_id}/outbound/draft",
        "/api/tickets/{ticket_id}/outbound/send",
        "/api/tickets/{ticket_id}/timeline",
        "/api/admin/queues/summary",
        "/api/admin/capabilities/catalog",
    ]


def _ticket_payload(ticket: Ticket, now) -> dict[str, Any]:
    due_candidates = [ensure_utc(value) for value in (ticket.first_response_due_at, ticket.resolution_due_at) if value is not None]
    due_at = min(due_candidates) if due_candidates else None
    due_at_utc = ensure_utc(due_at)
    now_utc = ensure_utc(now)
    return {
        "id": ticket.id,
        "ticket_no": ticket.ticket_no,
        "title": ticket.issue_summary or ticket.title,
        "status": _enum_value(ticket.status),
        "priority": _enum_value(ticket.priority),
        "source_channel": _enum_value(ticket.source_channel),
        "customer_name": ticket.customer.name if ticket.customer else None,
        "assignee_name": ticket.assignee.display_name if ticket.assignee else None,
        "team_name": ticket.team.name if ticket.team else None,
        "first_response_due_at": ticket.first_response_due_at,
        "resolution_due_at": ticket.resolution_due_at,
        "next_due_at": due_at,
        "required_action": ticket.required_action,
        "updated_at": ticket.updated_at,
        "overdue": bool(due_at_utc and now_utc and due_at_utc < now_utc),
    }


def build_today_workbench(db: Session, current_user: User) -> dict[str, Any]:
    ensure_capability(current_user, CAP_TICKET_READ, db, message="today_workbench_requires_ticket_read")
    capabilities = resolve_capabilities(current_user, db)
    now = utc_now()
    sla_window_end = now + timedelta(minutes=30)

    active_query = _active_visible_tickets(db, current_user)
    sla_query = _visible_tickets(db, current_user).filter(_sla_risk_filter(sla_window_end))
    handoff_query = db.query(WebchatHandoffRequest).join(Ticket, Ticket.id == WebchatHandoffRequest.ticket_id).filter(
        WebchatHandoffRequest.status == "requested"
    )
    for condition in _visible_ticket_filters(current_user):
        handoff_query = handoff_query.filter(condition)

    metrics = {
        "visible_open_tickets": _count(active_query),
        "my_open_tickets": _count(active_query.filter(Ticket.assignee_id == current_user.id)),
        "sla_risk_30m": _count(sla_query),
        "customer_waiting": _count(active_query.filter(Ticket.status == TicketStatus.waiting_customer)),
        "unassigned_visible": _count(active_query.filter(Ticket.assignee_id.is_(None))),
        "urgent_open": _count(active_query.filter(Ticket.priority == TicketPriority.urgent)),
        "webchat_waiting": _count(
            active_query.filter(
                Ticket.source_channel == SourceChannel.web_chat,
                or_(Ticket.status.in_(PENDING_HUMAN_STATUSES), Ticket.conversation_state.in_(WEBCHAT_ATTENTION_STATES)),
            )
        ),
        "webchat_handoff_requested": _count(handoff_query),
        "email_waiting": _count(
            active_query.filter(
                Ticket.source_channel == SourceChannel.email,
                Ticket.status.in_((*PENDING_HUMAN_STATUSES, TicketStatus.in_progress)),
            )
        ),
    }

    tasks = [
        _task(
            key="handoff",
            title="待人工接入",
            count=metrics["webchat_handoff_requested"],
            severity="danger" if metrics["webchat_handoff_requested"] else "warning",
            source="/api/webchat/admin/handoff/queue?view=requested",
            next_step="先接入等待最久且 AI 已暂停的会话",
            target_route="/webchat",
        ),
        _task(
            key="my-tickets",
            title="我的处理中工单",
            count=metrics["my_open_tickets"],
            severity="processing",
            source="/api/lite/cases?assignee_id=me",
            next_step="按 SLA 剩余时间排序处理",
            target_route="/workspace",
            target_filter={"assignee_id": current_user.id},
        ),
        _task(
            key="sla-risk",
            title="30 分钟内 SLA 风险",
            count=metrics["sla_risk_30m"],
            severity="danger" if metrics["sla_risk_30m"] else "success",
            source="/api/workbench/today#sla_risk_tickets",
            next_step="先回复客户或升级组长",
            target_route="/workspace",
            target_filter={"sla": "risk_30m"},
        ),
        _task(
            key="customer-waiting",
            title="客户已回复待处理",
            count=metrics["customer_waiting"],
            severity="warning" if metrics["customer_waiting"] else "success",
            source="/api/lite/cases?status=waiting_customer",
            next_step="避免客户二次催问",
            target_route="/workspace",
            target_filter={"status": TicketStatus.waiting_customer.value},
        ),
        _task(
            key="webchat-waiting",
            title="WebChat 待接入/待回复",
            count=metrics["webchat_waiting"],
            severity="warning" if metrics["webchat_waiting"] else "success",
            source="/api/webchat/admin/conversations",
            next_step="接入等待最久的 WebChat",
            target_route="/webchat",
        ),
        _task(
            key="email-waiting",
            title="等待中的 Email",
            count=metrics["email_waiting"],
            severity="warning" if metrics["email_waiting"] else "success",
            source="/api/lite/cases?source_channel=email",
            next_step="优先处理需要客户回复的邮件队列",
            target_route="/email",
            target_filter={"source_channel": SourceChannel.email.value},
        ),
    ]
    if CAP_TICKET_ASSIGN in capabilities:
        tasks.append(
            _task(
                key="unassigned",
                title="未分配队列",
                count=metrics["unassigned_visible"],
                severity="warning" if metrics["unassigned_visible"] else "success",
                source="/api/lite/cases?assignee_id=null",
                next_step="按语言、市场、负载分配给 Agent",
                target_route="/workspace",
                target_filter={"assignee_id": None},
            )
        )

    sla_rows = (
        sla_query.options(
            joinedload(Ticket.customer),
            joinedload(Ticket.assignee),
            joinedload(Ticket.team),
        )
        .order_by(func.coalesce(Ticket.first_response_due_at, Ticket.resolution_due_at).asc(), Ticket.updated_at.desc())
        .limit(8)
        .all()
    )

    return {
        "generated_at": now,
        "user": {
            "id": current_user.id,
            "username": current_user.username,
            "display_name": current_user.display_name,
            "email": current_user.email,
            "role": current_user.role,
            "team_id": current_user.team_id,
            "capabilities": sorted(capabilities),
        },
        "role_label": ROLE_LABELS.get(current_user.role, current_user.role.value),
        "mission": ROLE_MISSIONS.get(current_user.role, ROLE_MISSIONS[UserRole.agent]),
        "metrics": metrics,
        "tasks": tasks,
        "visible_entrypoints": _visible_entrypoints(capabilities),
        "interaction_states": _interaction_states(),
        "command_center": _command_center(capabilities),
        "sla_risk_tickets": [_ticket_payload(ticket, now) for ticket in sla_rows],
        "permissions": {
            "can_assign": CAP_TICKET_ASSIGN in capabilities,
            "can_manage_runtime": CAP_RUNTIME_MANAGE in capabilities,
            "can_read_tickets": CAP_TICKET_READ in capabilities,
        },
        "source_contracts": _source_contracts(),
    }
