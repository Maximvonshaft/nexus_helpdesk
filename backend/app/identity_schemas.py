from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .schemas import AuthUserRead, LoginResponse


class AuthSessionUserRead(AuthUserRead):
    must_change_password: bool = False
    password_changed_at: datetime | None = None
    last_login_at: datetime | None = None
    mfa_enabled: bool = False


class AuthSessionResponse(LoginResponse):
    user: AuthSessionUserRead


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=4096)
    new_password: str = Field(min_length=1, max_length=4096)


class PasswordChangeResponse(BaseModel):
    ok: bool
    reauthenticate: bool


class MfaLoginChallengeRead(BaseModel):
    mfa_required: Literal[True] = True
    challenge_token: str
    expires_in_seconds: int = 300
    display_name: str


class MfaLoginVerifyRequest(BaseModel):
    challenge_token: str = Field(min_length=1, max_length=4096)
    credential: str = Field(min_length=1, max_length=128)


class MfaStatusRead(BaseModel):
    enabled: bool
    setup_pending: bool
    confirmed_at: datetime | None = None
    last_verified_at: datetime | None = None
    recovery_codes_remaining: int = 0


class MfaSetupBeginRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=4096)


class MfaSetupBeginRead(BaseModel):
    secret: str
    otpauth_uri: str


class MfaSetupConfirmRequest(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class MfaSensitiveActionRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=4096)
    credential: str = Field(min_length=1, max_length=128)


class MfaRecoveryCodesRead(BaseModel):
    ok: bool = True
    recovery_codes: list[str] = Field(default_factory=list)
    reauthenticate: bool = True


class MfaActionRead(BaseModel):
    ok: bool = True
    reauthenticate: bool = False


class CredentialPolicyRead(BaseModel):
    user_id: int
    username: str
    display_name: str
    role: str
    is_active: bool
    must_change_password: bool
    password_changed_at: datetime | None = None
    last_login_at: datetime | None = None
    mfa_enabled: bool = False
    mfa_confirmed_at: datetime | None = None
    mfa_last_verified_at: datetime | None = None
    mfa_recovery_codes_remaining: int = 0
    updated_at: datetime | None = None
