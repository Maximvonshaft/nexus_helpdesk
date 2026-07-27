from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import false, or_
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
from ..services.scope_permissions import has_global_case_visibility
from ..services.tenant_query_authority import actor_tenant_query_scope
from .deps import get_current_user

router = APIRouter(prefix="/api/lookups", tags=["lookups"])


@router.get("/users", response_model=list[UserRead])
def list_users(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    scope = actor_tenant_query_scope(db, current_user)
    query = scope.users(db).filter(User.is_active.is_(True))
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
    scope = actor_tenant_query_scope(db, current_user)
    query = scope.teams(db).filter(Team.is_active.is_(True))
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
    scope = actor_tenant_query_scope(db, current_user)
    query = scope.markets(db).filter(Market.is_active.is_(True))
    if not has_global_case_visibility(current_user, db):
        team = (
            scope.teams(db)
            .filter(Team.id == current_user.team_id)
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
    scope = actor_tenant_query_scope(db, current_user)
    query = db.query(MarketBulletin).filter(
        MarketBulletin.market_id.in_(scope.active_market_ids())
    )
    if not has_global_case_visibility(current_user, db):
        team = (
            scope.teams(db)
            .filter(Team.id == current_user.team_id)
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
    del current_user
    # Tag is the canonical immutable platform taxonomy. Tenant-owned mutable
    # workflow data remains scoped at Ticket and TicketTag authorities.
    return db.query(Tag).order_by(Tag.name.asc(), Tag.id.asc()).all()


@router.get("/ai-configs", response_model=list[AIConfigResourceRead])
def list_ai_configs(
    config_type: str | None = None,
    market_id: int | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    scope = actor_tenant_query_scope(db, current_user)
    if market_id is not None:
        market = (
            scope.markets(db)
            .filter(
                Market.id == market_id,
                Market.is_active.is_(True),
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
