from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import Ticket, User
from .business_outcome_metrics import build_business_outcome_metrics
from .permissions import (
    CAP_AI_CONFIG_MANAGE,
    CAP_AI_CONFIG_READ,
    CAP_BULLETIN_MANAGE,
    CAP_CHANNEL_ACCOUNT_MANAGE,
    CAP_RUNTIME_MANAGE,
    CAP_TICKET_ASSIGN,
    CAP_USER_MANAGE,
    resolve_capabilities,
)
from .processing_purpose_enforcement import (
    PURPOSE_ANALYTICS,
    restricted_customer_ids,
)
from .scope_permissions import has_global_case_visibility
from .tenant_authority import resolve_actor_tenant_id

CONTROL_TOWER_READ_CAPABILITIES = {
    CAP_TICKET_ASSIGN,
    CAP_BULLETIN_MANAGE,
    CAP_CHANNEL_ACCOUNT_MANAGE,
    CAP_RUNTIME_MANAGE,
    CAP_AI_CONFIG_READ,
    CAP_AI_CONFIG_MANAGE,
    CAP_USER_MANAGE,
}


def build_business_outcome_metrics_for_user(
    db: Session,
    current_user: User,
):
    capabilities = resolve_capabilities(current_user, db)
    if not capabilities.intersection(CONTROL_TOWER_READ_CAPABILITIES):
        raise HTTPException(
            status_code=403,
            detail="control_tower_requires_management_capability",
        )
    tenant_id = resolve_actor_tenant_id(db, current_user)
    if tenant_id is None:
        raise HTTPException(
            status_code=403,
            detail="control_tower_requires_tenant_authority",
        )
    visible = db.query(Ticket).filter(Ticket.tenant_id == tenant_id)
    restricted_ids = restricted_customer_ids(
        db,
        tenant_id=tenant_id,
        purpose=PURPOSE_ANALYTICS,
    )
    if restricted_ids:
        visible = visible.filter(
            or_(
                Ticket.customer_id.is_(None),
                Ticket.customer_id.notin_(restricted_ids),
            )
        )
    if not has_global_case_visibility(current_user, db):
        visible = visible.filter(
            or_(
                Ticket.team_id == current_user.team_id,
                Ticket.assignee_id == current_user.id,
            )
        )
    return build_business_outcome_metrics(
        db,
        visible_query=visible,
        tenant_id=tenant_id,
    )
