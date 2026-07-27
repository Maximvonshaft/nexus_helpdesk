from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import false, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Market, MarketBulletin, Tag, Team, User
from ..schemas import (
    AIConfigResourceRead,
    MarketBulletinRead,
    MarketRead,
    TagRead,
    TeamRead,
    UserRead,
)
from ..services.ai_config_service import list_published_resources
from ..services.identity_tenant_scope import actor_tenant_id
from ..services.scope_permissions import has_global_case_visibility
from .deps import get_current_user

router = APIRouter(prefix="/api/lookups", tags=["lookups"])


def _tenant_filter(model, tenant_id: int | None):
    column = getattr(model, "tenant_id")
    return column == tenant_id if tenant_id is not None else column.is_(None)


def _actor_market_ids(db: Session, current_user) -> tuple[int | None, object]:
    tenant_id = actor_tenant_id(db, current_user)
    market_ids = select(Market.id).where(
        _tenant_filter(Market, tenant_id),
        Market.is_active.is_(True),
    )
    return tenant_id, market_ids


@router.get("/users", response_model=list[UserRead])
def list_users(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    tenant_id = actor_tenant_id(db, current_user)
    query = db.query(User).filter(
        User.is_active.is_(True),
        _tenant_filter(User, tenant_id),
    )
    if not has_global_case_visibility(current_user, db):
        query = query.filter(
            or_(
                User.team_id == current_user.team_id,
                User.id == current_user.id,
            )
        )
    return query.order_by(User.display_name.asc(), User.id.asc()).all()


@router.get("/teams", response_model=list[TeamRead])
def list_teams(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    tenant_id = actor_tenant_id(db, current_user)
    query = db.query(Team).filter(
        Team.is_active.is_(True),
        _tenant_filter(Team, tenant_id),
    )
    if not has_global_case_visibility(current_user, db):
        query = (
            query.filter(Team.id == current_user.team_id)
            if current_user.team_id is not None
            else query.filter(false())
        )
    return query.order_by(Team.name.asc(), Team.id.asc()).all()


@router.get("/markets", response_model=list[MarketRead])
def list_markets(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    tenant_id = actor_tenant_id(db, current_user)
    query = db.query(Market).filter(
        Market.is_active.is_(True),
        _tenant_filter(Market, tenant_id),
    )
    if not has_global_case_visibility(current_user, db):
        team = (
            db.query(Team)
            .filter(
                Team.id == current_user.team_id,
                _tenant_filter(Team, tenant_id),
            )
            .first()
            if current_user.team_id is not None
            else None
        )
        query = (
            query.filter(Market.id == team.market_id)
            if team is not None and team.market_id is not None
            else query.filter(false())
        )
    return [
        MarketRead.model_validate(row)
        for row in query.order_by(
            Market.country_code.asc(),
            Market.name.asc(),
            Market.id.asc(),
        ).all()
    ]


@router.get("/bulletins", response_model=list[MarketBulletinRead])
def list_operator_bulletins(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    tenant_id, market_ids = _actor_market_ids(db, current_user)
    query = db.query(MarketBulletin).filter(
        MarketBulletin.market_id.in_(market_ids)
    )
    if not has_global_case_visibility(current_user, db):
        team = (
            db.query(Team)
            .filter(
                Team.id == current_user.team_id,
                _tenant_filter(Team, tenant_id),
            )
            .first()
            if current_user.team_id is not None
            else None
        )
        query = (
            query.filter(MarketBulletin.market_id == team.market_id)
            if team is not None and team.market_id is not None
            else query.filter(false())
        )
    rows = query.order_by(
        MarketBulletin.is_active.desc(),
        MarketBulletin.updated_at.desc(),
        MarketBulletin.id.desc(),
    ).all()
    return [MarketBulletinRead.model_validate(row) for row in rows]


@router.get("/tags", response_model=list[TagRead])
def list_tags(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    # Tag is the canonical platform taxonomy and has no Tenant-owned mutable
    # fields. Ticket visibility remains Tenant-scoped at the Ticket authority.
    return db.query(Tag).order_by(Tag.name.asc(), Tag.id.asc()).all()


@router.get("/ai-configs", response_model=list[AIConfigResourceRead])
def list_ai_configs(
    config_type: str | None = None,
    market_id: int | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    tenant_id = actor_tenant_id(db, current_user)
    if market_id is not None:
        market = (
            db.query(Market)
            .filter(
                Market.id == market_id,
                Market.is_active.is_(True),
                _tenant_filter(Market, tenant_id),
            )
            .first()
        )
        if market is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="market_not_found",
            )
    rows = list_published_resources(
        db,
        config_type=config_type,
        market_id=market_id,
    )
    return [AIConfigResourceRead.model_validate(row) for row in rows]
