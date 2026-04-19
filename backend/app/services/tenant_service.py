from __future__ import annotations

import re
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session, joinedload

from ..models import Ticket, Customer, Team, ChannelAccount, MarketBulletin, AIConfigResource, OpenClawConversationLink, User
from ..multi_tenant_models import (
    AIConfigResourceTenantLink,
    ChannelAccountTenantLink,
    CustomerTenantLink,
    MarketBulletinTenantLink,
    OpenClawConversationTenantLink,
    TeamTenantLink,
    Tenant,
    TenantAIProfile,
    TenantKnowledgeEntry,
    TenantMembership,
    TicketTenantLink,
)


SLUG_RE = re.compile(r'[^a-z0-9]+')


def normalize_tenant_slug(value: str) -> str:
    slug = SLUG_RE.sub('-', (value or '').strip().lower()).strip('-')
    if not slug:
        raise HTTPException(status_code=400, detail='Invalid tenant slug')
    return slug


def list_user_tenant_options(db: Session, user: User) -> list[dict]:
    rows = (
        db.query(TenantMembership)
        .options(joinedload(TenantMembership.tenant))
        .filter(TenantMembership.user_id == user.id, TenantMembership.is_active.is_(True))
        .order_by(TenantMembership.is_default.desc(), TenantMembership.id.asc())
        .all()
    )
    return [
        {
            'tenant': row.tenant,
            'membership_role': row.membership_role,
            'is_default': row.is_default,
        }
        for row in rows
        if row.tenant is not None
    ]


def resolve_current_tenant(db: Session, user: User, requested_tenant_id: int | None = None) -> Tenant:
    memberships = list_user_tenant_options(db, user)
    if not memberships:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='No tenant membership found for current user')

    if requested_tenant_id is not None:
        for item in memberships:
            if item['tenant'].id == requested_tenant_id:
                return item['tenant']
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Requested tenant is not accessible for current user')

    for item in memberships:
        if item['is_default']:
            return item['tenant']
    return memberships[0]['tenant']


def create_tenant(db: Session, *, slug: str, name: str, external_ref: str | None = None, owner_user_id: int | None = None) -> Tenant:
    tenant = Tenant(slug=normalize_tenant_slug(slug), name=name.strip(), external_ref=external_ref)
    db.add(tenant)
    db.flush()
    if owner_user_id is not None:
        db.add(TenantMembership(tenant_id=tenant.id, user_id=owner_user_id, membership_role='owner', is_default=False, is_active=True))
        db.flush()
    return tenant


def ensure_user_membership(db: Session, *, tenant_id: int, user_id: int, membership_role: str = 'member', is_default: bool = False) -> TenantMembership:
    row = (
        db.query(TenantMembership)
        .filter(TenantMembership.tenant_id == tenant_id, TenantMembership.user_id == user_id)
        .first()
    )
    if row is None:
        row = TenantMembership(
            tenant_id=tenant_id,
            user_id=user_id,
            membership_role=membership_role,
            is_default=is_default,
            is_active=True,
        )
        db.add(row)
        db.flush()
    else:
        row.membership_role = membership_role or row.membership_role
        row.is_active = True
        if is_default:
            row.is_default = True
    if is_default:
        db.query(TenantMembership).filter(TenantMembership.user_id == user_id, TenantMembership.id != row.id).update({'is_default': False})
    return row


def get_or_create_tenant_ai_profile(db: Session, tenant_id: int) -> TenantAIProfile:
    row = db.query(TenantAIProfile).filter(TenantAIProfile.tenant_id == tenant_id).first()
    if row is None:
        row = TenantAIProfile(
            tenant_id=tenant_id,
            display_name='Support Assistant',
            brand_name=None,
            tone_style='professional',
            forbidden_claims=['Do not invent tracking updates', 'Do not promise refunds or compensation without approval'],
            escalation_policy='Escalate billing, legal and compensation promises to a human supervisor.',
            signature_style='Best regards',
            language_policy='Reply in the customer language when confidently detected, otherwise use English.',
            system_context={'product_scope': 'customer support'},
            allowed_actions=['draft_reply', 'summarize', 'classify'],
            enable_auto_reply=True,
            enable_auto_summary=True,
            enable_auto_classification=True,
        )
        db.add(row)
        db.flush()
    return row


def upsert_tenant_ai_profile(db: Session, tenant_id: int, payload: dict) -> TenantAIProfile:
    row = get_or_create_tenant_ai_profile(db, tenant_id)
    for key, value in payload.items():
        if hasattr(row, key):
            setattr(row, key, value)
    db.flush()
    return row


def list_tenant_knowledge_entries(db: Session, tenant_id: int, *, active_only: bool = False) -> list[TenantKnowledgeEntry]:
    query = db.query(TenantKnowledgeEntry).filter(TenantKnowledgeEntry.tenant_id == tenant_id)
    if active_only:
        query = query.filter(TenantKnowledgeEntry.is_active.is_(True))
    return query.order_by(TenantKnowledgeEntry.priority.asc(), TenantKnowledgeEntry.updated_at.desc()).all()


def create_tenant_knowledge_entry(db: Session, tenant_id: int, payload: dict) -> TenantKnowledgeEntry:
    row = TenantKnowledgeEntry(tenant_id=tenant_id, **payload)
    db.add(row)
    db.flush()
    return row


def update_tenant_knowledge_entry(db: Session, tenant_id: int, entry_id: int, payload: dict) -> TenantKnowledgeEntry:
    row = db.query(TenantKnowledgeEntry).filter(TenantKnowledgeEntry.tenant_id == tenant_id, TenantKnowledgeEntry.id == entry_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail='Tenant knowledge entry not found')
    for key, value in payload.items():
        setattr(row, key, value)
    db.flush()
    return row


def attach_ticket_to_tenant(db: Session, *, ticket_id: int, tenant_id: int) -> TicketTenantLink:
    row = db.query(TicketTenantLink).filter(TicketTenantLink.ticket_id == ticket_id).first()
    if row is None:
        row = TicketTenantLink(ticket_id=ticket_id, tenant_id=tenant_id)
        db.add(row)
        db.flush()
    return row


def attach_customer_to_tenant(db: Session, *, customer_id: int, tenant_id: int) -> CustomerTenantLink:
    row = db.query(CustomerTenantLink).filter(CustomerTenantLink.customer_id == customer_id, CustomerTenantLink.tenant_id == tenant_id).first()
    if row is None:
        row = CustomerTenantLink(customer_id=customer_id, tenant_id=tenant_id)
        db.add(row)
        db.flush()
    return row


def attach_team_to_tenant(db: Session, *, team_id: int, tenant_id: int) -> TeamTenantLink:
    row = db.query(TeamTenantLink).filter(TeamTenantLink.team_id == team_id).first()
    if row is None:
        row = TeamTenantLink(team_id=team_id, tenant_id=tenant_id)
        db.add(row)
        db.flush()
    else:
        row.tenant_id = tenant_id
    return row


def attach_channel_account_to_tenant(db: Session, *, channel_account_id: int, tenant_id: int) -> ChannelAccountTenantLink:
    row = db.query(ChannelAccountTenantLink).filter(ChannelAccountTenantLink.channel_account_id == channel_account_id).first()
    if row is None:
        row = ChannelAccountTenantLink(channel_account_id=channel_account_id, tenant_id=tenant_id)
        db.add(row)
        db.flush()
    else:
        row.tenant_id = tenant_id
    return row


def attach_bulletin_to_tenant(db: Session, *, bulletin_id: int, tenant_id: int) -> MarketBulletinTenantLink:
    row = db.query(MarketBulletinTenantLink).filter(MarketBulletinTenantLink.bulletin_id == bulletin_id).first()
    if row is None:
        row = MarketBulletinTenantLink(bulletin_id=bulletin_id, tenant_id=tenant_id)
        db.add(row)
        db.flush()
    else:
        row.tenant_id = tenant_id
    return row


def attach_ai_config_to_tenant(db: Session, *, resource_id: int, tenant_id: int) -> AIConfigResourceTenantLink:
    row = db.query(AIConfigResourceTenantLink).filter(AIConfigResourceTenantLink.resource_id == resource_id).first()
    if row is None:
        row = AIConfigResourceTenantLink(resource_id=resource_id, tenant_id=tenant_id)
        db.add(row)
        db.flush()
    else:
        row.tenant_id = tenant_id
    return row


def attach_openclaw_conversation_to_tenant(db: Session, *, conversation_id: int, tenant_id: int) -> OpenClawConversationTenantLink:
    row = db.query(OpenClawConversationTenantLink).filter(OpenClawConversationTenantLink.conversation_id == conversation_id).first()
    if row is None:
        row = OpenClawConversationTenantLink(conversation_id=conversation_id, tenant_id=tenant_id)
        db.add(row)
        db.flush()
    else:
        row.tenant_id = tenant_id
    return row


def get_tenant_ticket_ids(db: Session, tenant_id: int) -> list[int]:
    return [row.ticket_id for row in db.query(TicketTenantLink.ticket_id).filter(TicketTenantLink.tenant_id == tenant_id).all()]


def get_tenant_ticket(db: Session, tenant_id: int, ticket_id: int) -> Ticket:
    ticket = (
        db.query(Ticket)
        .join(TicketTenantLink, TicketTenantLink.ticket_id == Ticket.id)
        .filter(TicketTenantLink.tenant_id == tenant_id, Ticket.id == ticket_id)
        .first()
    )
    if ticket is None:
        raise HTTPException(status_code=404, detail='Ticket not found in current tenant')
    return ticket


def build_tenant_ai_runtime_context(db: Session, tenant_id: int) -> dict:
    profile = get_or_create_tenant_ai_profile(db, tenant_id)
    entries = list_tenant_knowledge_entries(db, tenant_id, active_only=True)[:10]
    return {
        'profile': profile,
        'knowledge_entries': entries,
    }


def tenant_id_for_ticket(db: Session, ticket_id: int) -> int | None:
    row = db.query(TicketTenantLink).filter(TicketTenantLink.ticket_id == ticket_id).first()
    return row.tenant_id if row else None


def tenant_id_for_team(db: Session, team_id: int | None) -> int | None:
    if team_id is None:
        return None
    row = db.query(TeamTenantLink).filter(TeamTenantLink.team_id == team_id).first()
    return row.tenant_id if row else None


def tenant_id_for_channel_account(db: Session, channel_account_id: int | None = None, account_id: str | None = None) -> int | None:
    if channel_account_id is not None:
        row = db.query(ChannelAccountTenantLink).filter(ChannelAccountTenantLink.channel_account_id == channel_account_id).first()
        if row is not None:
            return row.tenant_id
    if account_id:
        channel_account = db.query(ChannelAccount).filter(ChannelAccount.account_id == account_id, ChannelAccount.is_active.is_(True)).first()
        if channel_account is not None:
            row = db.query(ChannelAccountTenantLink).filter(ChannelAccountTenantLink.channel_account_id == channel_account.id).first()
            if row is not None:
                return row.tenant_id
    return None


def tenant_id_for_conversation(db: Session, conversation_id: int) -> int | None:
    row = db.query(OpenClawConversationTenantLink).filter(OpenClawConversationTenantLink.conversation_id == conversation_id).first()
    return row.tenant_id if row else None


def tenant_summary(db: Session, tenant_id: int) -> dict:
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant is None:
        raise HTTPException(status_code=404, detail='Tenant not found')
    membership = db.query(TenantMembership).filter(TenantMembership.tenant_id == tenant_id, TenantMembership.is_active.is_(True)).count()
    knowledge_count = db.query(TenantKnowledgeEntry).filter(TenantKnowledgeEntry.tenant_id == tenant_id).count()
    ticket_count = db.query(TicketTenantLink).filter(TicketTenantLink.tenant_id == tenant_id).count()
    return {
        'tenant': tenant,
        'membership_count': membership,
        'knowledge_entry_count': knowledge_count,
        'ticket_count': ticket_count,
    }
