from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from ..enums import ConversationState, JobStatus, MessageStatus, SourceChannel, TicketStatus, UserRole
from ..models import (
    AIConfigResource,
    AdminAuditLog,
    BackgroundJob,
    ChannelAccount,
    MarketBulletin,
    OutboundEmailAccount,
    Team,
    Ticket,
    TicketOutboundMessage,
    User,
    UserCapabilityOverride,
)
from ..operator_models import OperatorTask
from ..utils.time import utc_now
from ..voice_models import WebchatVoiceSession
from ..webchat_models import WebchatConversation
from .audit_service import log_admin_audit
from .operator_queue import create_operator_task
from .permissions import (
    CAP_AI_CONFIG_MANAGE,
    CAP_AI_CONFIG_READ,
    CAP_BULLETIN_MANAGE,
    CAP_CHANNEL_ACCOUNT_MANAGE,
    CAP_RUNTIME_MANAGE,
    CAP_SPEEDAF_ADDRESS_UPDATE_WRITE,
    CAP_SPEEDAF_CANCEL_WRITE,
    CAP_SPEEDAF_WORK_ORDER_WRITE,
    CAP_TICKET_ASSIGN,
    CAP_TICKET_READ,
    CAP_USER_MANAGE,
    CAP_WEBCALL_VOICE_QUEUE_VIEW,
    CAP_WEBCHAT_HANDOFF_ACCEPT,
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
ACTIVE_VOICE_STATUSES = ("created", "ringing", "accepted", "active")
EMAIL_MARKERS = ("email", "mail", "smtp", "imap", "pop3")
HEALTHY_ACCOUNT_STATUSES = ("healthy", "ok", "ready")
SLA_RISK_WINDOW = timedelta(minutes=30)
CONTROL_TOWER_CAPABILITIES = {
    CAP_TICKET_ASSIGN,
    CAP_BULLETIN_MANAGE,
    CAP_CHANNEL_ACCOUNT_MANAGE,
    CAP_RUNTIME_MANAGE,
    CAP_AI_CONFIG_READ,
    CAP_AI_CONFIG_MANAGE,
    CAP_USER_MANAGE,
}
SPEEDAF_WRITE_CAPABILITIES = {CAP_SPEEDAF_WORK_ORDER_WRITE, CAP_SPEEDAF_ADDRESS_UPDATE_WRITE, CAP_SPEEDAF_CANCEL_WRITE}
CONTROL_TOWER_ACTIONS: dict[str, dict[str, str]] = {
    "assign-unassigned": {"capability": CAP_TICKET_ASSIGN, "label": "调度未分配队列", "href": "/workspace"},
    "clear-sla-risk": {"capability": CAP_TICKET_ASSIGN, "label": "处理 SLA 风险", "href": "/workspace"},
    "publish-bulletin": {"capability": CAP_BULLETIN_MANAGE, "label": "更新公告口径", "href": "/bulletins"},
    "recover-runtime": {"capability": CAP_RUNTIME_MANAGE, "label": "恢复 dead 队列", "href": "/runtime"},
    "fix-email-route": {"capability": CAP_CHANNEL_ACCOUNT_MANAGE, "label": "修复 Email 线路", "href": "/outbound-email"},
    "review-ai-rules": {"capability": CAP_AI_CONFIG_MANAGE, "label": "复核 AI 配置", "href": "/ai-control"},
    "provider-ops": {"capability": CAP_CHANNEL_ACCOUNT_MANAGE, "label": "巡检渠道账号", "href": "/accounts"},
    "speedaf-wizard": {"capability": CAP_SPEEDAF_WORK_ORDER_WRITE, "label": "复核 Speedaf 高危动作", "href": "/workspace"},
}


def _value(raw: Any) -> Any:
    return raw.value if hasattr(raw, "value") else raw


def _visible_ticket_query(db: Session, user: User):
    query = db.query(Ticket)
    if user.role not in {UserRole.admin, UserRole.manager, UserRole.auditor}:
        query = query.filter(or_(Ticket.team_id == user.team_id, Ticket.assignee_id == user.id))
    return query


def _visible_operator_task_query(db: Session, user: User):
    query = db.query(OperatorTask)
    if user.role not in {UserRole.admin, UserRole.manager, UserRole.auditor}:
        query = query.outerjoin(Ticket, OperatorTask.ticket_id == Ticket.id).filter(
            or_(Ticket.team_id == user.team_id, Ticket.assignee_id == user.id, OperatorTask.assignee_id == user.id)
        )
    return query


def _active_tickets(query):
    return query.filter(Ticket.status.in_(ACTIVE_TICKET_STATUSES))


def _count(query, column) -> int:
    return int(query.with_entities(func.count(column)).scalar() or 0)


def _sla_risk_filter(now: datetime):
    deadline = now + SLA_RISK_WINDOW
    return or_(
        Ticket.first_response_due_at <= deadline,
        Ticket.resolution_due_at <= deadline,
        Ticket.first_response_breached.is_(True),
        Ticket.resolution_breached.is_(True),
    )


def _email_case_filter():
    predicates = [Ticket.source_channel == SourceChannel.email]
    for marker in EMAIL_MARKERS:
        like = f"%{marker}%"
        predicates.extend([Ticket.category.ilike(like), Ticket.sub_category.ilike(like), Ticket.preferred_reply_channel.ilike(like)])
    return or_(*predicates)


def _active_bulletin_query(db: Session, now: datetime):
    return db.query(MarketBulletin).filter(
        MarketBulletin.is_active.is_(True),
        or_(MarketBulletin.starts_at.is_(None), MarketBulletin.starts_at <= now),
        or_(MarketBulletin.ends_at.is_(None), MarketBulletin.ends_at >= now),
    )


def _tone(value: int, *, danger: int, warning: int = 1) -> str:
    if value >= danger:
        return "danger"
    if value >= warning:
        return "warning"
    return "success"


def _kpi(key: str, label: str, value: int, hint: str, tone: str = "default") -> dict[str, Any]:
    return {"key": key, "label": label, "value": value, "hint": hint, "tone": tone}


def _action(key: str, label: str, count: int, tone: str, next_step: str, href: str, capability: str, capabilities: set[str], task: OperatorTask | None = None) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "count": count,
        "tone": tone,
        "next": next_step,
        "href": href,
        "capability": capability,
        "enabled": capability in capabilities,
        "action_task_id": task.id if task else None,
        "action_status": task.status if task else None,
    }


def _lane(key: str, area: str, value: int, risk: str, next_step: str, href: str, capability: str, capabilities: set[str]) -> dict[str, Any]:
    return {
        "key": key,
        "area": area,
        "value": value,
        "risk": risk,
        "next": next_step,
        "href": href,
        "capability": capability,
        "enabled": capability in capabilities,
    }


def _channel(key: str, label: str, health: str, queue: int, risk: int, href: str, capability: str, capabilities: set[str]) -> dict[str, Any]:
    if health == "danger":
        tone = "danger"
    elif risk > 0 or health == "warning":
        tone = "warning"
    else:
        tone = "success"
    return {
        "key": key,
        "label": label,
        "health": tone,
        "queue": queue,
        "risk": risk,
        "href": href,
        "capability": capability,
        "enabled": capability in capabilities,
    }


def _template_block(key: str, label: str, backend_contract: str, status_value: str, evidence: str, href: str) -> dict[str, str]:
    return {"key": key, "label": label, "backend_contract": backend_contract, "status": status_value, "evidence": evidence, "href": href}


def _active_control_tower_actions(db: Session, user: User) -> dict[str, OperatorTask]:
    rows = (
        _visible_operator_task_query(db, user)
        .filter(OperatorTask.task_type == "control_tower_action", OperatorTask.status.notin_(TERMINAL_TASK_STATUSES))
        .order_by(OperatorTask.updated_at.desc(), OperatorTask.id.desc())
        .all()
    )
    tasks: dict[str, OperatorTask] = {}
    for task in rows:
        if task.source_id:
            tasks.setdefault(task.source_id, task)
    return tasks


def _dead_runtime_count(db: Session) -> int:
    jobs = int(db.query(func.count(BackgroundJob.id)).filter(BackgroundJob.status == JobStatus.dead).scalar() or 0)
    outbound = int(db.query(func.count(TicketOutboundMessage.id)).filter(TicketOutboundMessage.status == MessageStatus.dead).scalar() or 0)
    return jobs + outbound


def submit_control_tower_action(db: Session, current_user: User, payload) -> dict[str, Any]:
    capabilities = resolve_capabilities(current_user, db)
    if not (capabilities & CONTROL_TOWER_CAPABILITIES):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="control_tower_requires_management_capability")

    action_key = str(payload.action_key).strip()
    action = CONTROL_TOWER_ACTIONS.get(action_key)
    if action is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="control_tower_action_not_found")
    required_capability = action["capability"]
    if required_capability not in capabilities:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="control_tower_action_requires_capability")

    label = payload.label or action["label"]
    href = payload.href or action["href"]
    count = int(payload.count or 0)
    note = payload.note or f"{label}: {count} current item(s)"
    task_payload = {
        "action_key": action_key,
        "label": label,
        "href": href,
        "count": count,
        "capability": required_capability,
        "note": note,
        "submitted_by": current_user.id,
        "submitted_at": utc_now().isoformat(),
    }
    task, created = create_operator_task(
        db,
        source_type="control_tower",
        task_type="control_tower_action",
        source_id=action_key,
        reason_code=action_key,
        priority=20 if count else 60,
        payload=task_payload,
        note=note,
    )
    task.assignee_id = current_user.id
    task.updated_at = utc_now()
    log_admin_audit(
        db,
        actor_id=current_user.id,
        action="control_tower.action.submitted",
        target_type="operator_task",
        target_id=task.id,
        new_value={"created": created, **task_payload},
    )
    db.flush()
    return {
        "ok": True,
        "task_id": task.id,
        "created": created,
        "status": task.status,
        "action_key": action_key,
        "submitted_at": task.updated_at,
    }


def _team_workload(db: Session, user: User, now: datetime) -> list[dict[str, Any]]:
    sla_risk = _sla_risk_filter(now)
    rows = (
        _active_tickets(_visible_ticket_query(db, user))
        .outerjoin(Team, Team.id == Ticket.team_id)
        .with_entities(
            Ticket.team_id,
            Team.name,
            func.count(Ticket.id).label("active_count"),
            func.sum(case((Ticket.assignee_id.is_(None), 1), else_=0)).label("unassigned_count"),
            func.sum(case((sla_risk, 1), else_=0)).label("sla_risk_count"),
            func.sum(case((or_(Ticket.first_response_breached.is_(True), Ticket.resolution_breached.is_(True), Ticket.first_response_due_at < now, Ticket.resolution_due_at < now), 1), else_=0)).label("overdue_count"),
        )
        .group_by(Ticket.team_id, Team.name)
        .order_by(func.count(Ticket.id).desc(), Team.name.asc())
        .limit(8)
        .all()
    )
    return [
        {
            "team_id": team_id,
            "team_name": name or "未分配团队",
            "active_tickets": int(active_count or 0),
            "unassigned": int(unassigned_count or 0),
            "sla_risk": int(sla_risk_count or 0),
            "overdue": int(overdue_count or 0),
        }
        for team_id, name, active_count, unassigned_count, sla_risk_count, overdue_count in rows
    ]


def _bulletin_impact(db: Session, now: datetime) -> list[dict[str, Any]]:
    rows = (
        _active_bulletin_query(db, now)
        .with_entities(MarketBulletin.severity, MarketBulletin.category, func.count(MarketBulletin.id))
        .group_by(MarketBulletin.severity, MarketBulletin.category)
        .order_by(func.count(MarketBulletin.id).desc(), MarketBulletin.severity.asc())
        .limit(8)
        .all()
    )
    return [
        {"severity": severity or "info", "category": category or "notice", "count": int(count or 0), "tone": _tone(int(count or 0), danger=3, warning=1)}
        for severity, category, count in rows
    ]


def build_control_tower(db: Session, current_user: User) -> dict[str, Any]:
    now = utc_now()
    capabilities = resolve_capabilities(current_user, db)
    if not (capabilities & CONTROL_TOWER_CAPABILITIES):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="control_tower_requires_management_capability")

    visible = _visible_ticket_query(db, current_user)
    active = _active_tickets(visible)
    active_count = _count(active, Ticket.id)
    unassigned = _count(_active_tickets(visible).filter(Ticket.assignee_id.is_(None)), Ticket.id)
    sla_risk = _count(_active_tickets(visible).filter(_sla_risk_filter(now)), Ticket.id)
    overdue = _count(
        _active_tickets(visible).filter(
            or_(Ticket.first_response_breached.is_(True), Ticket.resolution_breached.is_(True), Ticket.first_response_due_at < now, Ticket.resolution_due_at < now)
        ),
        Ticket.id,
    )
    ready_to_reply = _count(
        _active_tickets(visible).filter(
            or_(
                Ticket.status == TicketStatus.waiting_internal,
                Ticket.conversation_state.in_((ConversationState.human_review_required, ConversationState.ready_to_reply, ConversationState.reopened_by_customer)),
            )
        ),
        Ticket.id,
    )
    email_cases = _count(_active_tickets(visible).filter(_email_case_filter()), Ticket.id)
    webchat_cases = _count(_active_tickets(visible).filter(Ticket.source_channel == SourceChannel.web_chat), Ticket.id)
    handoff_count = _count(
        _visible_operator_task_query(db, current_user).filter(OperatorTask.task_type == "handoff", OperatorTask.status.notin_(TERMINAL_TASK_STATUSES)),
        OperatorTask.id,
    )
    active_webcalls = int(
        db.query(func.count(WebchatVoiceSession.id))
        .filter(WebchatVoiceSession.mode != "internal_ai_demo", WebchatVoiceSession.status.in_(ACTIVE_VOICE_STATUSES), WebchatVoiceSession.ended_at.is_(None))
        .scalar()
        or 0
    )
    open_webchat_conversations = int(db.query(func.count(WebchatConversation.id)).filter(WebchatConversation.status == "open").scalar() or 0)
    active_bulletins = int(_active_bulletin_query(db, now).with_entities(func.count(MarketBulletin.id)).scalar() or 0)
    critical_bulletins = int(_active_bulletin_query(db, now).filter(MarketBulletin.severity.in_(("critical", "urgent", "high"))).with_entities(func.count(MarketBulletin.id)).scalar() or 0)
    dead_runtime = _dead_runtime_count(db)
    pending_outbound = int(db.query(func.count(TicketOutboundMessage.id)).filter(TicketOutboundMessage.status.in_((MessageStatus.pending, MessageStatus.processing))).scalar() or 0)
    dead_outbound = int(db.query(func.count(TicketOutboundMessage.id)).filter(TicketOutboundMessage.status == MessageStatus.dead).scalar() or 0)
    active_email_accounts = int(db.query(func.count(OutboundEmailAccount.id)).filter(OutboundEmailAccount.is_active.is_(True)).scalar() or 0)
    risky_email_accounts = int(
        db.query(func.count(OutboundEmailAccount.id))
        .filter(
            OutboundEmailAccount.is_active.is_(True),
            or_(
                OutboundEmailAccount.health_status.is_(None),
                OutboundEmailAccount.health_status.notin_(HEALTHY_ACCOUNT_STATUSES),
                OutboundEmailAccount.last_test_status.in_(("failed", "error")),
            ),
        )
        .scalar()
        or 0
    )
    risky_channel_accounts = int(
        db.query(func.count(ChannelAccount.id))
        .filter(
            ChannelAccount.is_active.is_(True),
            or_(ChannelAccount.health_status.is_(None), ChannelAccount.health_status.notin_(HEALTHY_ACCOUNT_STATUSES)),
        )
        .scalar()
        or 0
    )
    draft_ai_configs = int(db.query(func.count(AIConfigResource.id)).filter(AIConfigResource.is_active.is_(True), AIConfigResource.published_version == 0).scalar() or 0)
    published_ai_configs = int(db.query(func.count(AIConfigResource.id)).filter(AIConfigResource.is_active.is_(True), AIConfigResource.published_version > 0).scalar() or 0)
    active_users = int(db.query(func.count(User.id)).filter(User.is_active.is_(True)).scalar() or 0)
    capability_overrides = int(db.query(func.count(UserCapabilityOverride.id)).scalar() or 0)
    recent_audit = int(db.query(func.count(AdminAuditLog.id)).filter(AdminAuditLog.created_at >= now - timedelta(hours=24)).scalar() or 0)
    active_control_actions = _active_control_tower_actions(db, current_user)
    speedaf_capability_count = len(capabilities & SPEEDAF_WRITE_CAPABILITIES)

    return {
        "generated_at": now.isoformat(),
        "role": _value(current_user.role),
        "user_id": current_user.id,
        "capabilities": sorted(capabilities),
        "kpis": [
            _kpi("active_tickets", "活动工单", active_count, "当前账号可见范围内未关闭工单", _tone(active_count, danger=80, warning=1)),
            _kpi("sla_risk", "SLA 风险", sla_risk, "30 分钟内到期或已违约", _tone(sla_risk, danger=3, warning=1)),
            _kpi("handoff_waiting", "WebChat 接管", handoff_count, "未关闭 handoff operator tasks", _tone(handoff_count, danger=5, warning=1)),
            _kpi("active_webcalls", "WebCall 通话", active_webcalls, "当前活动或振铃 WebCall", _tone(active_webcalls, danger=5, warning=1)),
            _kpi("runtime_dead", "运行异常", dead_runtime, "dead jobs + dead outbound", _tone(dead_runtime, danger=1, warning=1)),
            _kpi("active_bulletins", "生效公告", active_bulletins, "当前会影响客服口径的公告", _tone(critical_bulletins, danger=1, warning=1 if active_bulletins else 99)),
        ],
        "manager_actions": [
            _action("assign-unassigned", "调度未分配队列", unassigned, _tone(unassigned, danger=10, warning=1), "按团队负载和 SLA 风险分配给 Agent", "/workspace", CAP_TICKET_ASSIGN, capabilities, active_control_actions.get("assign-unassigned")),
            _action("clear-sla-risk", "处理 SLA 风险", sla_risk, _tone(sla_risk, danger=3, warning=1), "进入工单台优先处理临近 SLA 工单", "/workspace", CAP_TICKET_ASSIGN, capabilities, active_control_actions.get("clear-sla-risk")),
            _action("publish-bulletin", "更新公告口径", critical_bulletins, _tone(critical_bulletins, danger=1, warning=1), "发布或调整影响客户回复的紧急公告", "/bulletins", CAP_BULLETIN_MANAGE, capabilities, active_control_actions.get("publish-bulletin")),
            _action("recover-runtime", "恢复 dead 队列", dead_runtime, _tone(dead_runtime, danger=1, warning=1), "进入运行恢复中心执行受控重排", "/runtime", CAP_RUNTIME_MANAGE, capabilities, active_control_actions.get("recover-runtime")),
            _action("fix-email-route", "修复 Email 线路", risky_email_accounts, _tone(risky_email_accounts, danger=1, warning=1), "检查 SMTP 账号测试、健康状态和发送失败", "/outbound-email", CAP_CHANNEL_ACCOUNT_MANAGE, capabilities, active_control_actions.get("fix-email-route")),
            _action("review-ai-rules", "复核 AI 配置", draft_ai_configs, _tone(draft_ai_configs, danger=3, warning=1), "发布、回滚或补齐 Persona / Knowledge / Policy", "/ai-control", CAP_AI_CONFIG_MANAGE, capabilities, active_control_actions.get("review-ai-rules")),
            _action("provider-ops", "巡检渠道账号", risky_email_accounts + risky_channel_accounts, _tone(risky_email_accounts + risky_channel_accounts, danger=1, warning=1), "生成渠道治理任务并进入账号健康维护", "/accounts", CAP_CHANNEL_ACCOUNT_MANAGE, capabilities, active_control_actions.get("provider-ops")),
            _action("speedaf-wizard", "复核 Speedaf 高危动作", speedaf_capability_count, _tone(speedaf_capability_count, danger=3, warning=1), "创建 Speedaf 高风险动作复核任务，再进入受控工单台处理", "/workspace", CAP_SPEEDAF_WORK_ORDER_WRITE, capabilities, active_control_actions.get("speedaf-wizard")),
        ],
        "team_workload": _team_workload(db, current_user, now),
        "channel_health": [
            _channel("webchat", "WebChat", "warning" if handoff_count else "success", webchat_cases + open_webchat_conversations, handoff_count, "/webchat", CAP_WEBCHAT_HANDOFF_ACCEPT, capabilities),
            _channel("webcall", "WebCall", "warning" if active_webcalls else "success", active_webcalls, active_webcalls, "/webcall", CAP_WEBCALL_VOICE_QUEUE_VIEW, capabilities),
            _channel("email", "Email", "warning" if risky_email_accounts or dead_outbound else "success", email_cases + pending_outbound, risky_email_accounts + dead_outbound, "/email", CAP_CHANNEL_ACCOUNT_MANAGE, capabilities),
            _channel("runtime", "Runtime", "danger" if dead_runtime else "success", pending_outbound, dead_runtime, "/runtime", CAP_RUNTIME_MANAGE, capabilities),
        ],
        "bulletin_impact": _bulletin_impact(db, now),
        "governance_lanes": [
            _lane("queue-load", "队列负载", active_count, _tone(sla_risk + overdue, danger=3, warning=1), "下钻工单队列和团队负载", "/workspace", CAP_TICKET_READ, capabilities),
            _lane("bulletin-impact", "公告影响", active_bulletins, _tone(critical_bulletins, danger=1, warning=1), "确认客服回复口径是否需要更新", "/bulletins", CAP_BULLETIN_MANAGE, capabilities),
            _lane("rbac-lens", "权限覆盖", capability_overrides, _tone(capability_overrides, danger=10, warning=1), f"活跃账号 {active_users}；复核高风险 capability override", "/users", CAP_USER_MANAGE, capabilities),
            _lane("provider-channel", "渠道账号", active_email_accounts + risky_channel_accounts, _tone(risky_email_accounts + risky_channel_accounts, danger=1, warning=1), "检查 Email/渠道账号健康和兜底线路", "/accounts", CAP_CHANNEL_ACCOUNT_MANAGE, capabilities),
            _lane("ai-governance", "AI 治理", published_ai_configs + draft_ai_configs, _tone(draft_ai_configs, danger=3, warning=1), "复核已发布配置和未发布草稿", "/ai-control", CAP_AI_CONFIG_MANAGE, capabilities),
            _lane("audit-safety", "审计活跃", recent_audit, _tone(recent_audit, danger=50, warning=1), "查看最近管理动作和运行恢复影响", "/runtime", CAP_RUNTIME_MANAGE, capabilities),
        ],
        "template_blocks": [
            _template_block("kpi-tower", "KPI / Tower Tabs", "/api/lite/control-tower", "implemented", "来自 tickets、operator_tasks、voice_sessions、runtime queues", "/control-tower"),
            _template_block("bulletin-impact", "Bulletin Impact", "/api/lookups/bulletins + market_bulletins", "implemented", "按 severity/category 聚合当前生效公告", "/bulletins"),
            _template_block("rbac-product", "RBAC Product Lens", "users + user_capability_overrides", "implemented", "展示 active users、override 数和受控入口", "/users"),
            _template_block("provider-ops", "Provider / Channel Ops", "POST /api/lite/control-tower/actions + channel_accounts + outbound_email_accounts", "implemented", "Control Tower 可创建渠道治理任务，并链接账号健康维护页面", "/accounts"),
            _template_block("speedaf-wizard", "Speedaf Wizard", "POST /api/lite/control-tower/actions + Speedaf capability gates", "implemented", "Control Tower 可创建 Speedaf 高风险动作复核任务，执行仍受 workspace capability gate 控制", "/workspace"),
            _template_block("empty-error-mobile", "Empty / Error / Mobile States", "frontend route state", "implemented", "页面使用 loading/error/empty state 和 responsive grid", "/control-tower"),
        ],
        "facts": {
            "unassigned": unassigned,
            "overdue": overdue,
            "ready_to_reply": ready_to_reply,
            "email_cases": email_cases,
            "webchat_cases": webchat_cases,
            "open_webchat_conversations": open_webchat_conversations,
            "pending_outbound": pending_outbound,
            "dead_outbound": dead_outbound,
            "active_email_accounts": active_email_accounts,
            "risky_email_accounts": risky_email_accounts,
            "risky_channel_accounts": risky_channel_accounts,
            "draft_ai_configs": draft_ai_configs,
            "published_ai_configs": published_ai_configs,
            "active_users": active_users,
            "capability_overrides": capability_overrides,
            "recent_admin_audit_24h": recent_audit,
            "speedaf_write_capabilities": sorted(capabilities & SPEEDAF_WRITE_CAPABILITIES),
            "active_control_tower_actions": len(active_control_actions),
            "control_tower_action_write_endpoint": "implemented",
        },
    }
