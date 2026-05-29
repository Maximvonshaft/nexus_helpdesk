from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import and_, func, or_, true
from sqlalchemy.orm import Query, Session

from ..api.deps import get_current_user
from ..db import get_db
from ..enums import ConversationState, JobStatus, MessageStatus, TicketPriority, TicketStatus, UserRole
from ..models import (
    AdminAuditLog,
    BackgroundJob,
    ChannelAccount,
    IntegrationRequestLog,
    OutboundEmailAccount,
    Ticket,
    TicketOutboundMessage,
)
from ..schemas import (
    TodayWorkbenchCommandRead,
    TodayWorkbenchEntrypointRead,
    TodayWorkbenchInteractionStateRead,
    TodayWorkbenchMetricRead,
    TodayWorkbenchRead,
    TodayWorkbenchTaskRead,
)
from ..services.permissions import CAP_TICKET_READ, ensure_capability
from ..utils.time import utc_now
from ..webchat_models import WebchatHandoffRequest

router = APIRouter(prefix="/api/today", tags=["today-workbench"])

OPEN_TICKET_STATUSES = (
    TicketStatus.new,
    TicketStatus.pending_assignment,
    TicketStatus.in_progress,
    TicketStatus.waiting_customer,
    TicketStatus.waiting_internal,
    TicketStatus.escalated,
)

HEALTHY_STATUSES = {"ok", "healthy", "success", "tested", "passed"}
UNHEALTHY_TEST_STATUSES = {"failed", "error", "blocked", "timeout"}

ROLE_LABELS = {
    UserRole.agent: "一线客服",
    UserRole.lead: "组长",
    UserRole.manager: "客服主管",
    UserRole.admin: "管理员",
    UserRole.auditor: "审计",
}

ROLE_MISSIONS = {
    UserRole.agent: "先处理自己的 WebChat handoff、客户回复和 SLA 风险，再补齐草稿、发送和工单时间线。",
    UserRole.lead: "先消化未分配队列和溢出的 handoff，再处理升级单与质量抽检。",
    UserRole.manager: "先看 SLA 与跨渠道风险，再确认重点线路、异常工单和运行恢复是否闭环。",
    UserRole.admin: "先确认 provider smoke、dead job、RBAC 和审计证据，再处理系统级阻塞。",
    UserRole.auditor: "先复核高风险动作、敏感信息与集成错误，再抽查时间线证据完整性。",
}


def _count(query: Query) -> int:
    return int(query.count() or 0)


def _ticket_visibility_clause(current_user):
    if current_user.role in {UserRole.admin, UserRole.manager, UserRole.auditor}:
        return true()
    clauses = [Ticket.assignee_id == current_user.id]
    if current_user.team_id:
        clauses.append(Ticket.team_id == current_user.team_id)
    return or_(*clauses)


def _visible_tickets(db: Session, current_user) -> Query:
    return db.query(Ticket).filter(_ticket_visibility_clause(current_user))


def _open_visible_tickets(db: Session, current_user) -> Query:
    return _visible_tickets(db, current_user).filter(Ticket.status.in_(OPEN_TICKET_STATUSES))


def _visible_handoff_requests(db: Session, current_user) -> Query:
    return (
        db.query(WebchatHandoffRequest)
        .join(Ticket, Ticket.id == WebchatHandoffRequest.ticket_id)
        .filter(_ticket_visibility_clause(current_user))
    )


def _risky_outbound_email_accounts(db: Session) -> int:
    return _count(
        db.query(OutboundEmailAccount).filter(
            OutboundEmailAccount.is_active.is_(True),
            or_(
                func.lower(OutboundEmailAccount.health_status).notin_(HEALTHY_STATUSES),
                func.lower(OutboundEmailAccount.last_test_status).in_(UNHEALTHY_TEST_STATUSES),
            ),
        )
    )


def _risky_channel_accounts(db: Session) -> int:
    return _count(
        db.query(ChannelAccount).filter(
            ChannelAccount.is_active.is_(True),
            func.lower(ChannelAccount.health_status).notin_(HEALTHY_STATUSES),
        )
    )


def _dead_runtime_items(db: Session) -> int:
    return _count(db.query(BackgroundJob).filter(BackgroundJob.status == JobStatus.dead)) + _count(
        db.query(TicketOutboundMessage).filter(TicketOutboundMessage.status == MessageStatus.dead)
    )


def _recent_admin_audit_items(db: Session) -> int:
    since = utc_now() - timedelta(days=7)
    return _count(
        db.query(AdminAuditLog).filter(
            AdminAuditLog.created_at >= since,
            or_(
                AdminAuditLog.action.ilike("%user%"),
                AdminAuditLog.action.ilike("%capability%"),
                AdminAuditLog.action.ilike("%rbac%"),
            ),
        )
    )


def _integration_error_items(db: Session) -> int:
    since = utc_now() - timedelta(days=1)
    return _count(
        db.query(IntegrationRequestLog).filter(
            IntegrationRequestLog.created_at >= since,
            or_(IntegrationRequestLog.status_code >= 400, IntegrationRequestLog.error_code.isnot(None)),
        )
    )


def _workbench_counts(db: Session, current_user) -> dict[str, int]:
    now = utc_now()
    soon = now + timedelta(hours=4)
    open_tickets = _open_visible_tickets(db, current_user)
    handoffs = _visible_handoff_requests(db, current_user)
    requested_handoffs = handoffs.filter(WebchatHandoffRequest.status == "requested")

    return {
        "open_tickets": _count(open_tickets),
        "my_tickets": _count(open_tickets.filter(Ticket.assignee_id == current_user.id)),
        "unassigned": _count(open_tickets.filter(Ticket.assignee_id.is_(None))),
        "handoff": _count(
            handoffs.filter(
                or_(
                    WebchatHandoffRequest.status == "requested",
                    and_(WebchatHandoffRequest.status == "accepted", WebchatHandoffRequest.assigned_agent_id == current_user.id),
                )
            )
        ),
        "handoff_overflow": _count(requested_handoffs.filter(WebchatHandoffRequest.requested_at <= now - timedelta(minutes=3))),
        "escalations": _count(open_tickets.filter(Ticket.status == TicketStatus.escalated)),
        "quality": _count(open_tickets.filter(Ticket.resolved_at.is_(None), Ticket.priority.in_((TicketPriority.high, TicketPriority.urgent)))),
        "sla_risk": _count(
            open_tickets.filter(
                or_(
                    Ticket.first_response_breached.is_(True),
                    Ticket.resolution_breached.is_(True),
                    and_(Ticket.resolution_due_at.isnot(None), Ticket.resolution_due_at <= soon),
                )
            )
        ),
        "customer_waiting": _count(
            open_tickets.filter(
                or_(
                    Ticket.conversation_state.in_(
                        (
                            ConversationState.human_review_required,
                            ConversationState.ready_to_reply,
                            ConversationState.reopened_by_customer,
                        )
                    ),
                    Ticket.status.in_((TicketStatus.pending_assignment, TicketStatus.waiting_internal)),
                )
            )
        ),
        "channel_risk": _risky_channel_accounts(db) + _risky_outbound_email_accounts(db),
        "email_risk": _risky_outbound_email_accounts(db),
        "dead_jobs": _dead_runtime_items(db),
        "rbac_review": _recent_admin_audit_items(db),
        "integration_errors": _integration_error_items(db),
    }


def _task(key: str, title: str, count: int, severity: str, source: str, next_step: str, route: str, description: str) -> TodayWorkbenchTaskRead:
    return TodayWorkbenchTaskRead(
        key=key,
        title=title,
        count=count,
        severity=severity,
        source=source,
        next=next_step,
        route=route,
        description=description,
    )


def _tasks_for_role(role: UserRole, counts: dict[str, int]) -> list[TodayWorkbenchTaskRead]:
    templates: dict[UserRole, list[tuple[str, str, str, str, str, str]]] = {
        UserRole.agent: [
            ("handoff", "WebChat handoff", "/api/webchat/admin/handoff/queue", "接受或释放 handoff", "/webchat", "客户等待人工接管的实时会话。"),
            ("my_tickets", "我的处理中工单", "/api/lite/cases", "进入工单处理", "/workspace", "当前账号可直接闭环的客户工单。"),
            ("sla_risk", "SLA 优先风险", "/api/today/workbench", "先处理临期与已超时", "/workspace", "从 resolution_due_at 与 breached 标记聚合。"),
            ("customer_waiting", "客户已回复/待复核", "/api/lite/cases", "补齐回复或内部说明", "/workspace", "等待客服继续处理的客户侧状态。"),
        ],
        UserRole.lead: [
            ("unassigned", "未分配工单", "/api/lite/cases", "分配给当班客服", "/workspace", "团队队列里尚未指定负责人的工单。"),
            ("handoff_overflow", "Handoff 溢出", "/api/webchat/admin/handoff/queue", "强制接管或重新分配", "/webchat", "超过 3 分钟仍无人接的 WebChat 请求。"),
            ("escalations", "升级单", "/api/lite/cases", "确认处理人和下一步", "/workspace", "需要组长决策或跨团队协作的工单。"),
            ("quality", "质量抽检", "/api/today/workbench", "抽查高优先级处理中单", "/workspace", "高优先级未解决工单的交付质量关注项。"),
        ],
        UserRole.manager: [
            ("sla_risk", "SLA breach/risk", "/api/today/workbench", "拉齐责任人与补救动作", "/workspace", "已超时或 4 小时内到期的可见工单。"),
            ("channel_risk", "跨渠道风险", "/api/admin/channel-accounts", "检查发送线路与账号健康", "/accounts", "Channel 与 Email 账号健康状态异常数。"),
            ("email_risk", "Email 线路风险", "/api/admin/outbound-email/accounts", "测试 SMTP 或切换兜底账号", "/outbound-email", "活跃 SMTP 账号健康或测试失败项。"),
            ("dead_jobs", "运行异常", "/api/admin/queues/summary", "进入运行恢复处理 dead 项", "/runtime", "dead jobs 与 dead outbound 消息总数。"),
        ],
        UserRole.admin: [
            ("channel_risk", "Provider smoke", "/api/admin/provider-runtime/status", "确认 provider 与渠道账号", "/runtime", "生产 provider/渠道健康的阻塞项。"),
            ("dead_jobs", "Dead jobs", "/api/admin/queues/summary", "重试或标记处理", "/runtime", "后台队列与 outbound dead 项。"),
            ("rbac_review", "RBAC review", "/api/admin/capabilities/catalog", "复核最近权限变更", "/users", "最近 7 天用户/RBAC 相关审计动作。"),
            ("integration_errors", "Integration errors", "/api/v1/integration/*", "检查外部集成失败", "/runtime", "最近 24 小时集成错误或 4xx/5xx 响应。"),
        ],
        UserRole.auditor: [
            ("rbac_review", "权限变更审计", "/api/admin/capabilities/catalog", "复核操作者与证据", "/users", "最近 7 天用户/RBAC 相关审计动作。"),
            ("integration_errors", "集成错误审计", "/api/v1/integration/*", "抽查失败请求与幂等键", "/runtime", "最近 24 小时集成错误或 4xx/5xx 响应。"),
            ("sla_risk", "SLA 证据抽查", "/api/tickets/{id}/timeline", "查看时间线是否闭环", "/workspace", "需要审计时间线证据的 SLA 风险单。"),
            ("dead_jobs", "运行恢复审计", "/api/admin/queues/summary", "确认恢复动作留痕", "/runtime", "运行恢复相关 dead 项。"),
        ],
    }
    items = []
    for key, title, source, next_step, route, description in templates.get(role, templates[UserRole.agent]):
        count = counts.get(key, 0)
        severity = "critical" if count >= 5 else "warning" if count > 0 else "info"
        items.append(_task(key, title, count, severity, source, next_step, route, description))
    return items


def _metrics(counts: dict[str, int]) -> list[TodayWorkbenchMetricRead]:
    return [
        TodayWorkbenchMetricRead(key="open_tickets", label="可见未闭环", value=counts["open_tickets"], hint="真实工单 open 状态聚合"),
        TodayWorkbenchMetricRead(key="handoff", label="WebChat handoff", value=counts["handoff"], hint="requested/accepted handoff"),
        TodayWorkbenchMetricRead(key="sla_risk", label="SLA 风险", value=counts["sla_risk"], hint="超时或 4 小时内到期"),
        TodayWorkbenchMetricRead(key="customer_waiting", label="客户待处理", value=counts["customer_waiting"], hint="客户已回复或待人工复核"),
        TodayWorkbenchMetricRead(key="channel_risk", label="渠道风险", value=counts["channel_risk"], hint="Channel/Email 健康异常"),
        TodayWorkbenchMetricRead(key="dead_jobs", label="运行恢复", value=counts["dead_jobs"], hint="dead job/outbound"),
    ]


def _visible_entrypoints(role: UserRole) -> list[TodayWorkbenchEntrypointRead]:
    base = [
        TodayWorkbenchEntrypointRead(key="workspace", label="处理工单", route="/workspace", hint="分配、回复、证据与时间线", source="/api/lite/cases"),
        TodayWorkbenchEntrypointRead(key="webchat", label="WebChat", route="/webchat", hint="handoff、AI 接管与实时回复", source="/api/webchat/admin/handoff/queue"),
        TodayWorkbenchEntrypointRead(key="webcall", label="WebCall", route="/webcall", hint="来电队列、客户身份与会话动作", source="/api/webchat/admin/voice/sessions"),
        TodayWorkbenchEntrypointRead(key="email", label="Email", route="/email", hint="邮件队列、草稿保存与外发", source="/api/tickets/{id}/outbound/*"),
    ]
    if role in {UserRole.admin, UserRole.manager}:
        base.extend(
            [
                TodayWorkbenchEntrypointRead(key="runtime", label="运行恢复", route="/runtime", hint="dead/requeue 与同步健康", source="/api/admin/queues/summary"),
                TodayWorkbenchEntrypointRead(key="accounts", label="发送线路", route="/accounts", hint="渠道账号与 fallback", source="/api/admin/channel-accounts"),
            ]
        )
    if role in {UserRole.admin, UserRole.auditor}:
        base.append(TodayWorkbenchEntrypointRead(key="users", label="账号权限", route="/users", hint="RBAC 与审计追踪", source="/api/admin/capabilities/catalog"))
    return base


def _interaction_states() -> list[TodayWorkbenchInteractionStateRead]:
    return [
        TodayWorkbenchInteractionStateRead(state="loading", operator_signal="显示骨架与刷新状态", product_rule="不得误报 0；保留上次可用上下文", source="React Query loading/error state"),
        TodayWorkbenchInteractionStateRead(state="empty", operator_signal="明确当前没有待办", product_rule="展示下一步入口，不让客服停在空页", source="/api/today/workbench"),
        TodayWorkbenchInteractionStateRead(state="error", operator_signal="显示可重试失败", product_rule="保留模块边界，不吞掉接口错误", source="统一 API client ApiError"),
        TodayWorkbenchInteractionStateRead(state="permission denied", operator_signal="入口仍解释权限语义", product_rule="RBAC 决定可见范围与动作能力", source="routeAccess + backend capabilities"),
        TodayWorkbenchInteractionStateRead(state="unsaved changes", operator_signal="草稿/动作必须显式保存", product_rule="Email draft/save 与 timeline 写回分离验证", source="/api/tickets/{id}/outbound/draft"),
    ]


def _command_center() -> list[TodayWorkbenchCommandRead]:
    return [
        TodayWorkbenchCommandRead(key="cmd-webchat", label="打开 WebChat handoff", route="/webchat", source="/api/webchat/admin/handoff/queue", audit="WebchatEvent + handoff decision"),
        TodayWorkbenchCommandRead(key="cmd-ticket", label="打开工单处理", route="/workspace", source="/api/lite/cases", audit="TicketEvent + timeline"),
        TodayWorkbenchCommandRead(key="cmd-email", label="保存/发送 Email", route="/email", source="/api/tickets/{id}/outbound/draft|send", audit="TicketOutboundMessage + TicketEvent"),
        TodayWorkbenchCommandRead(key="cmd-trace", label="查看 timeline/audit", route="/workspace", source="/api/tickets/{id}/timeline", audit="Ticket timeline"),
        TodayWorkbenchCommandRead(key="cmd-rbac", label="复核 RBAC", route="/users", source="/api/admin/capabilities/catalog", audit="AdminAuditLog"),
    ]


@router.get("/workbench", response_model=TodayWorkbenchRead)
def get_today_workbench(db: Session = Depends(get_db), current_user=Depends(get_current_user)) -> TodayWorkbenchRead:
    ensure_capability(current_user, CAP_TICKET_READ, db)
    counts = _workbench_counts(db, current_user)
    return TodayWorkbenchRead(
        role=current_user.role,
        role_label=ROLE_LABELS.get(current_user.role, current_user.role.value),
        mission=ROLE_MISSIONS.get(current_user.role, ROLE_MISSIONS[UserRole.agent]),
        generated_at=utc_now(),
        metrics=_metrics(counts),
        tasks=_tasks_for_role(current_user.role, counts),
        visible_entrypoints=_visible_entrypoints(current_user.role),
        interaction_states=_interaction_states(),
        command_center=_command_center(),
        source_contracts=[
            "/api/today/workbench",
            "/api/lite/cases",
            "/api/webchat/admin/handoff/queue",
            "/api/webchat/admin/voice/sessions",
            "/api/tickets/{id}/outbound/draft",
            "/api/tickets/{id}/outbound/send",
            "/api/tickets/{id}/timeline",
            "/api/admin/queues/summary",
            "/api/admin/capabilities/catalog",
        ],
    )
