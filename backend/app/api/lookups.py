from fastapi import APIRouter, Depends
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import UserRole
from ..models import AIConfigResource, Market, MarketBulletin, Tag, Team, User
from ..schemas import AIConfigResourceRead, MarketBulletinRead, MarketRead, TagRead, TeamRead, UserRead
from ..services.ai_config_service import list_published_resources
from .deps import get_current_user

router = APIRouter(prefix="/api/lookups", tags=["lookups"])


@router.get("/users", response_model=list[UserRead])
def list_users(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    query = db.query(User).filter(User.is_active.is_(True))
    if current_user.role not in {UserRole.admin, UserRole.manager, UserRole.auditor}:
        query = query.filter(or_(User.team_id == current_user.team_id, User.id == current_user.id))
    return query.order_by(User.display_name.asc()).all()


@router.get("/teams", response_model=list[TeamRead])
def list_teams(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    query = db.query(Team).filter(Team.is_active.is_(True))
    if current_user.role not in {UserRole.admin, UserRole.manager, UserRole.auditor} and current_user.team_id is not None:
        query = query.filter(Team.id == current_user.team_id)
    return query.order_by(Team.name.asc()).all()




@router.get("/markets", response_model=list[MarketRead])
def list_markets(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    query = db.query(Market).filter(Market.is_active.is_(True))
    if current_user.role not in {UserRole.admin, UserRole.manager, UserRole.auditor} and current_user.team_id is not None:
        query = query.order_by(Market.country_code.asc(), Market.name.asc())
    return [MarketRead.model_validate(row) for row in query.order_by(Market.country_code.asc(), Market.name.asc()).all()]


@router.get("/bulletins", response_model=list[MarketBulletinRead])
def list_operator_bulletins(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    rows = db.query(MarketBulletin).order_by(MarketBulletin.is_active.desc(), MarketBulletin.updated_at.desc()).all()
    return [MarketBulletinRead.model_validate(row) for row in rows]


@router.get("/tags", response_model=list[TagRead])
def list_tags(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return db.query(Tag).order_by(Tag.name.asc()).all()


@router.get("/ai-configs", response_model=list[AIConfigResourceRead])
def list_ai_configs(config_type: str | None = None, market_id: int | None = None, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    rows = list_published_resources(db, config_type=config_type, market_id=market_id)
    return [AIConfigResourceRead.model_validate(row) for row in rows]
