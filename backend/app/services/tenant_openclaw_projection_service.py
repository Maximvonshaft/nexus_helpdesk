from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models import ChannelAccount
from ..multi_tenant_models import ChannelAccountTenantLink, Tenant, TenantAIProfile, TenantKnowledgeEntry
from ..openclaw_projection_models import TenantOpenClawAgent
from ..utils.time import utc_now
from .tenant_service import get_or_create_tenant_ai_profile, list_tenant_knowledge_entries


def _slugify(value: str) -> str:
    return ''.join(ch if ch.isalnum() else '-' for ch in value.lower()).strip('-') or 'tenant'


def _projection_agent_id(tenant: Tenant) -> str:
    return f"tenant-{tenant.id}-{_slugify(tenant.slug)}"


def _projection_workspace_dir(tenant: Tenant) -> str:
    return f"~/.openclaw/workspace-tenants/{tenant.slug}"


def _knowledge_projection_lines(entries: list[TenantKnowledgeEntry], max_items: int = 5) -> list[str]:
    lines: list[str] = []
    for entry in entries[:max_items]:
        lines.append(f"- [{entry.category}] {entry.title}")
    return lines


def render_identity_preview(profile: TenantAIProfile) -> str:
    brand = profile.brand_name or 'Tenant Brand'
    display = profile.display_name or 'Support Assistant'
    forbidden = '\n'.join(f"- {item}" for item in (profile.forbidden_claims or [])) or '- Do not invent facts'
    return (
        f"# IDENTITY\n\n"
        f"You are {display}, the customer support representative for {brand}.\n\n"
        f"## Tone\n{profile.tone_style or 'professional'}\n\n"
        f"## Role Prompt\n{profile.role_prompt or 'Be helpful, clear, and professional.'}\n\n"
        f"## Escalation Policy\n{profile.escalation_policy or 'Escalate sensitive topics to a human supervisor.'}\n\n"
        f"## Forbidden Claims\n{forbidden}\n\n"
        f"## Language Policy\n{profile.language_policy or 'Reply in the customer language when confidently detected, otherwise use English.'}\n"
    )


def render_bootstrap_preview(profile: TenantAIProfile, entries: list[TenantKnowledgeEntry]) -> str:
    short_rules = _knowledge_projection_lines(entries)
    rule_block = '\n'.join(short_rules) if short_rules else '- No curated bootstrap knowledge projected yet.'
    return (
        f"# BOOTSTRAP\n\n"
        f"This tenant keeps long-form SOP and FAQ content in NexusDesk tenant knowledge storage.\n"
        f"Only short, stable persona and routing guidance should be projected into the OpenClaw bootstrap layer.\n\n"
        f"## Stable tenant support references\n{rule_block}\n"
    )


def _resolve_binding_summary(db: Session, tenant_id: int) -> dict[str, Any]:
    rows = (
        db.query(ChannelAccount)
        .join(ChannelAccountTenantLink, ChannelAccountTenantLink.channel_account_id == ChannelAccount.id)
        .filter(ChannelAccountTenantLink.tenant_id == tenant_id, ChannelAccount.is_active.is_(True))
        .order_by(ChannelAccount.priority.asc(), ChannelAccount.id.asc())
        .all()
    )
    bindings = [
        {
            'channel': row.channel,
            'account_id': row.account_id,
            'market_id': row.market_id,
            'display_name': row.display_name,
            'is_default': idx == 0,
        }
        for idx, row in enumerate(rows)
    ]
    return {
        'bindings': bindings,
        'default_channel': bindings[0]['channel'] if bindings else None,
        'default_account_id': bindings[0]['account_id'] if bindings else None,
    }


def get_or_create_tenant_openclaw_projection(db: Session, tenant: Tenant) -> TenantOpenClawAgent:
    row = db.query(TenantOpenClawAgent).filter(TenantOpenClawAgent.tenant_id == tenant.id).first()
    if row is None:
        row = TenantOpenClawAgent(
            tenant_id=tenant.id,
            openclaw_agent_id=_projection_agent_id(tenant),
            agent_name=f"{tenant.name} Support",
            workspace_dir=_projection_workspace_dir(tenant),
            deployment_mode='shared_gateway',
            binding_scope='tenant_default',
            identity_sync_status='pending',
            knowledge_sync_status='pending',
            is_active=True,
        )
        db.add(row)
        db.flush()
    return row


def project_tenant_to_openclaw_runtime(db: Session, tenant_id: int) -> TenantOpenClawAgent:
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant is None:
        raise ValueError('Tenant not found')
    profile = get_or_create_tenant_ai_profile(db, tenant.id)
    entries = list_tenant_knowledge_entries(db, tenant.id, active_only=True)
    projection = get_or_create_tenant_openclaw_projection(db, tenant)
    projection.agent_name = f"{tenant.name} Support"
    projection.openclaw_agent_id = _projection_agent_id(tenant)
    projection.workspace_dir = _projection_workspace_dir(tenant)
    projection.binding_summary = _resolve_binding_summary(db, tenant.id)
    projection.identity_preview = render_identity_preview(profile)
    projection.bootstrap_preview = render_bootstrap_preview(profile, entries)
    projection.identity_sync_status = 'projected'
    projection.knowledge_sync_status = 'projected'
    projection.last_projection_error = None
    projection.last_projected_at = utc_now()
    db.flush()
    return projection


def build_ticket_openclaw_route_context(db: Session, tenant_id: int | None) -> dict[str, Any]:
    if tenant_id is None:
        return {'projection': None, 'default_account_id': None, 'default_channel': None}
    projection = db.query(TenantOpenClawAgent).filter(TenantOpenClawAgent.tenant_id == tenant_id, TenantOpenClawAgent.is_active.is_(True)).first()
    if projection is None:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if tenant is None:
            return {'projection': None, 'default_account_id': None, 'default_channel': None}
        projection = get_or_create_tenant_openclaw_projection(db, tenant)
        db.flush()
    binding_summary = projection.binding_summary or {}
    return {
        'projection': projection,
        'default_account_id': binding_summary.get('default_account_id'),
        'default_channel': binding_summary.get('default_channel'),
    }
