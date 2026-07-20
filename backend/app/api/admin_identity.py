from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import UserRole
from ..identity_schemas import CredentialPolicyRead
from ..models import Market, Team, User
from ..models_identity_policy import UserCredentialPolicy
from ..services.audit_service import log_admin_audit
from ..services.credential_policy_service import (
    advance_user_identity_version,
    ensure_credential_policy,
    require_password_change as require_password_change_policy,
)
from ..services.permissions import ROLE_CAPABILITIES, ensure_can_manage_users
from ..unit_of_work import managed_session
from ..utils.time import utc_now
from .deps import get_current_user

router = APIRouter(prefix="/api/admin/identity", tags=["admin-identity"])


class IdentityModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RolePolicyRead(IdentityModel):
    role: UserRole
    default_capabilities: list[str] = Field(default_factory=list)


class TeamCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    team_type: str = Field(default="support", min_length=1, max_length=80)
    market_id: int | None = None

    @field_validator("name", "team_type")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value cannot be blank")
        return cleaned


class TeamUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    team_type: str | None = Field(default=None, min_length=1, max_length=80)
    market_id: int | None = None
    is_active: bool | None = None

    @field_validator("name", "team_type")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value cannot be blank")
        return cleaned


class TeamGovernanceRead(IdentityModel):
    id: int
    name: str
    team_type: str
    market_id: int | None = None
    is_active: bool
    active_users: int = 0
    created_at: datetime
    updated_at: datetime


class UserTeamClearResponse(BaseModel):
    ok: bool
    user_id: int
    team_id: None = None


class IdentityActionResponse(BaseModel):
    ok: bool
    user_id: int


def _user_or_404(db: Session, user_id: int) -> User:
    row = db.query(User).filter(User.id == user_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return row


def _ensure_market(db: Session, market_id: int | None) -> None:
    if market_id is None:
        return
    if db.query(Market).filter(Market.id == market_id, Market.is_active.is_(True)).first() is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Market not found or inactive")


def _ensure_unique_team_name(db: Session, name: str, *, exclude_team_id: int | None = None) -> None:
    query = db.query(Team).filter(func.lower(Team.name) == name.strip().lower())
    if exclude_team_id is not None:
        query = query.filter(Team.id != exclude_team_id)
    if query.first() is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Team name already exists")


def _active_user_counts(db: Session) -> dict[int, int]:
    rows = (
        db.query(User.team_id, func.count(User.id))
        .filter(User.team_id.is_not(None), User.is_active.is_(True))
        .group_by(User.team_id)
        .all()
    )
    return {int(team_id): int(count or 0) for team_id, count in rows if team_id is not None}


def _serialize_team(row: Team, active_users: int) -> TeamGovernanceRead:
    return TeamGovernanceRead(
        id=row.id,
        name=row.name,
        team_type=row.team_type,
        market_id=row.market_id,
        is_active=row.is_active,
        active_users=active_users,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _serialize_policy(user_id: int, row: UserCredentialPolicy | None) -> CredentialPolicyRead:
    return CredentialPolicyRead(
        user_id=user_id,
        must_change_password=bool(row.must_change_password) if row is not None else False,
        password_changed_at=row.password_changed_at if row is not None else None,
        last_login_at=row.last_login_at if row is not None else None,
        updated_at=row.updated_at if row is not None else None,
    )


@router.get("/roles", response_model=list[RolePolicyRead])
def list_role_policies(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    return [
        RolePolicyRead(role=role, default_capabilities=sorted(ROLE_CAPABILITIES.get(role, set())))
        for role in UserRole
    ]


@router.get("/credential-policies", response_model=list[CredentialPolicyRead])
def list_credential_policies(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    user_ids = [user_id for (user_id,) in db.query(User.id).order_by(User.id.asc()).all()]
    policies = {
        row.user_id: row
        for row in db.query(UserCredentialPolicy).order_by(UserCredentialPolicy.user_id.asc()).all()
    }
    return [_serialize_policy(user_id, policies.get(user_id)) for user_id in user_ids]


@router.post("/users/{user_id}/require-password-change", response_model=IdentityActionResponse)
def require_user_password_change(
    user_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    target = _user_or_404(db, user_id)
    with managed_session(db):
        previous = ensure_credential_policy(db, target.id)
        old_value = {"must_change_password": previous.must_change_password}
        require_password_change_policy(db, target.id)
        advance_user_identity_version(target)
        db.flush()
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action="user.password_change_required",
            target_type="user",
            target_id=target.id,
            old_value=old_value,
            new_value={"must_change_password": True, "sessions_revoked": True},
        )
        db.flush()
    return IdentityActionResponse(ok=True, user_id=target.id)


@router.post("/users/{user_id}/revoke-sessions", response_model=IdentityActionResponse)
def revoke_user_sessions(
    user_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    target = _user_or_404(db, user_id)
    with managed_session(db):
        advance_user_identity_version(target)
        db.flush()
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action="user.sessions_revoked",
            target_type="user",
            target_id=target.id,
            old_value=None,
            new_value={"all_sessions_revoked": True},
        )
        db.flush()
    return IdentityActionResponse(ok=True, user_id=target.id)


@router.get("/teams", response_model=list[TeamGovernanceRead])
def list_identity_teams(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    counts = _active_user_counts(db)
    rows = db.query(Team).order_by(Team.is_active.desc(), Team.name.asc(), Team.id.asc()).all()
    return [_serialize_team(row, counts.get(row.id, 0)) for row in rows]


@router.post("/teams", response_model=TeamGovernanceRead)
def create_identity_team(
    payload: TeamCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    _ensure_unique_team_name(db, payload.name)
    _ensure_market(db, payload.market_id)
    with managed_session(db):
        row = Team(
            name=payload.name,
            team_type=payload.team_type,
            market_id=payload.market_id,
            is_active=True,
        )
        db.add(row)
        db.flush()
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action="team.create",
            target_type="team",
            target_id=row.id,
            old_value=None,
            new_value={
                "name": row.name,
                "team_type": row.team_type,
                "market_id": row.market_id,
                "is_active": row.is_active,
            },
        )
    db.refresh(row)
    return _serialize_team(row, 0)


@router.patch("/teams/{team_id}", response_model=TeamGovernanceRead)
def update_identity_team(
    team_id: int,
    payload: TeamUpdateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    row = db.query(Team).filter(Team.id == team_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        _ensure_unique_team_name(db, data["name"], exclude_team_id=row.id)
    if "market_id" in data:
        _ensure_market(db, data["market_id"])
    active_users = int(
        db.query(func.count(User.id))
        .filter(User.team_id == row.id, User.is_active.is_(True))
        .scalar()
        or 0
    )
    if data.get("is_active") is False and active_users:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Move active users before deactivating this team",
        )

    before = {
        "name": row.name,
        "team_type": row.team_type,
        "market_id": row.market_id,
        "is_active": row.is_active,
    }
    with managed_session(db):
        for key, value in data.items():
            setattr(row, key, value)
        row.updated_at = utc_now()
        db.flush()
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action="team.update",
            target_type="team",
            target_id=row.id,
            old_value=before,
            new_value={
                "name": row.name,
                "team_type": row.team_type,
                "market_id": row.market_id,
                "is_active": row.is_active,
            },
        )
    db.refresh(row)
    return _serialize_team(row, active_users)


@router.delete("/users/{user_id}/team", response_model=UserTeamClearResponse)
def clear_user_team(
    user_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    row = _user_or_404(db, user_id)
    previous_team_id = row.team_id
    with managed_session(db):
        row.team_id = None
        row.updated_at = utc_now()
        db.flush()
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action="user.team_cleared",
            target_type="user",
            target_id=row.id,
            old_value={"team_id": previous_team_id},
            new_value={"team_id": None},
        )
    return UserTeamClearResponse(ok=True, user_id=row.id, team_id=None)
