from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .schemas import AuthUserRead, LoginResponse


class AuthSessionUserRead(AuthUserRead):
    must_change_password: bool = False
    password_changed_at: datetime | None = None
    last_login_at: datetime | None = None


class AuthSessionResponse(LoginResponse):
    user: AuthSessionUserRead


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=4096)
    new_password: str = Field(min_length=1, max_length=4096)


class PasswordChangeResponse(BaseModel):
    ok: bool
    reauthenticate: bool


class CredentialPolicyRead(BaseModel):
    user_id: int
    username: str
    display_name: str
    role: str
    is_active: bool
    must_change_password: bool
    password_changed_at: datetime | None = None
    last_login_at: datetime | None = None
    updated_at: datetime | None = None
