from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from .enums import UserRole
from .schemas import AuthUserRead, LoginResponse


class AuthSessionUserRead(AuthUserRead):
    must_change_password: bool = False
    password_changed_at: datetime | None = None
    last_login_at: datetime | None = None


class AuthSessionResponse(LoginResponse):
    user: AuthSessionUserRead


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class AccountSecurityRead(BaseModel):
    user_id: int
    session_version: int
    must_change_password: bool
    password_changed_at: datetime | None = None
    last_login_at: datetime | None = None
    updated_at: datetime | None = None


UserSecurityStateRead = AccountSecurityRead


class RoleProfileRead(BaseModel):
    role: UserRole
    capabilities: list[str]


class UserTeamAssignmentRequest(BaseModel):
    team_id: int | None = None
