from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth_service import create_access_token, hash_password, verify_password
from ..db import get_db
from ..identity_schemas import (
    AuthSessionResponse,
    AuthSessionUserRead,
    MfaActionRead,
    MfaLoginChallengeRead,
    MfaLoginVerifyRequest,
    MfaRecoveryCodesRead,
    MfaSensitiveActionRequest,
    MfaSetupBeginRead,
    MfaSetupBeginRequest,
    MfaSetupConfirmRequest,
    MfaStatusRead,
    PasswordChangeRequest,
    PasswordChangeResponse,
)
from ..models import User
from ..schemas import LoginRequest
from ..services.audit_service import log_admin_audit
from ..services.auth_throttle import build_login_throttle_key, clear_login_failures, enforce_login_allowed, record_login_failure
from ..services.credential_policy_service import (
    advance_user_identity_version,
    complete_password_change,
    credential_policy_payload,
    ensure_credential_policy,
    record_successful_login,
)
from ..services.mfa_service import (
    begin_mfa_setup,
    cancel_mfa_setup,
    clear_mfa,
    confirm_mfa_setup,
    create_mfa_challenge_token,
    decode_mfa_challenge_token,
    mfa_status_payload,
    regenerate_recovery_codes,
    verify_mfa_credential,
)
from ..services.password_policy import PasswordPolicyError, validate_admin_password_policy
from ..services.permissions import capability_fingerprint, resolve_capabilities
from ..unit_of_work import managed_session
from ..utils.client_ip import get_client_ip
from .deps import get_authenticated_user, get_current_user

router = APIRouter(prefix='/api/auth', tags=['auth'])


def _session_user_for(user: User, db: Session) -> AuthSessionUserRead:
    policy = credential_policy_payload(db, user.id)
    return AuthSessionUserRead(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        role=user.role,
        team_id=user.team_id,
        capabilities=sorted(resolve_capabilities(user, db)),
        must_change_password=policy['must_change_password'],
        password_changed_at=policy['password_changed_at'],
        last_login_at=policy['last_login_at'],
        mfa_enabled=policy['mfa_enabled'],
    )


def _login_response_for_user(user: User, db: Session) -> AuthSessionResponse:
    token = create_access_token(
        user.id,
        user.updated_at,
        policy_fingerprint=capability_fingerprint(user, db),
    )
    return AuthSessionResponse(
        access_token=token,
        user=_session_user_for(user, db),
    )


def _user_version_matches(user: User, challenge_version) -> bool:
    current = user.updated_at
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    return current == challenge_version


def _service_unavailable(exc: RuntimeError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=str(exc),
    )


def _verify_password_or_400(current_user: User, password: str) -> None:
    if not verify_password(password, current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Current password is incorrect')


def _verify_sensitive_mfa_action(
    db: Session,
    current_user: User,
    payload: MfaSensitiveActionRequest,
):
    _verify_password_or_400(current_user, payload.current_password)
    policy = ensure_credential_policy(db, current_user.id)
    if not policy.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='MFA is not enabled')
    try:
        method = verify_mfa_credential(db, policy, payload.credential)
    except RuntimeError as exc:
        raise _service_unavailable(exc) from exc
    if method is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid MFA credential')
    return policy, method


@router.post('/login', response_model=AuthSessionResponse | MfaLoginChallengeRead)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    username = payload.username.strip()
    throttle_key = build_login_throttle_key(username, get_client_ip(request))
    enforce_login_allowed(db, throttle_key)
    user = db.query(User).filter(func.lower(User.username) == username.lower(), User.is_active.is_(True)).first()
    if not user or not verify_password(payload.password, user.password_hash):
        with managed_session(db):
            record_login_failure(db, throttle_key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid credentials')

    policy = ensure_credential_policy(db, user.id)
    if policy.mfa_enabled:
        return MfaLoginChallengeRead(
            challenge_token=create_mfa_challenge_token(user),
            display_name=user.display_name or user.username,
        )

    with managed_session(db):
        clear_login_failures(db, throttle_key)
        record_successful_login(db, user.id)
        db.flush()
    return _login_response_for_user(user, db)


@router.post('/mfa/login/verify', response_model=AuthSessionResponse)
def verify_mfa_login(
    payload: MfaLoginVerifyRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    claims = decode_mfa_challenge_token(payload.challenge_token)
    if claims is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid or expired MFA challenge')
    user = db.query(User).filter(User.id == claims.user_id, User.is_active.is_(True)).first()
    if user is None or not _user_version_matches(user, claims.user_version):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid or expired MFA challenge')

    throttle_key = build_login_throttle_key(user.username, get_client_ip(request))
    enforce_login_allowed(db, throttle_key)
    policy = ensure_credential_policy(db, user.id)
    try:
        method = verify_mfa_credential(db, policy, payload.credential)
    except RuntimeError as exc:
        db.rollback()
        raise _service_unavailable(exc) from exc

    if method is None:
        db.rollback()
        with managed_session(db):
            record_login_failure(db, throttle_key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid MFA credential')

    with managed_session(db):
        clear_login_failures(db, throttle_key)
        record_successful_login(db, user.id)
        if method == 'recovery_code':
            log_admin_audit(
                db,
                actor_id=user.id,
                action='auth.mfa_recovery_code_used',
                target_type='user',
                target_id=user.id,
                old_value=None,
                new_value={'recovery_codes_remaining': mfa_status_payload(policy)['recovery_codes_remaining']},
            )
        db.flush()
    return _login_response_for_user(user, db)


@router.get('/me', response_model=AuthSessionUserRead)
def me(current_user: User = Depends(get_authenticated_user), db: Session = Depends(get_db)):
    return _session_user_for(current_user, db)


@router.post('/change-password', response_model=PasswordChangeResponse)
def change_password(
    payload: PasswordChangeRequest,
    current_user: User = Depends(get_authenticated_user),
    db: Session = Depends(get_db),
):
    _verify_password_or_400(current_user, payload.current_password)
    if verify_password(payload.new_password, current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='New password must be different')
    try:
        validate_admin_password_policy(payload.new_password)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    with managed_session(db):
        current_user.password_hash = hash_password(payload.new_password)
        advance_user_identity_version(current_user)
        db.flush()
        complete_password_change(db, current_user.id)
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action='auth.password_changed',
            target_type='user',
            target_id=current_user.id,
            old_value=None,
            new_value={'reauthentication_required': True},
        )
        db.flush()
    return PasswordChangeResponse(ok=True, reauthenticate=True)


@router.post('/logout-all')
def logout_all_sessions(
    current_user: User = Depends(get_authenticated_user),
    db: Session = Depends(get_db),
):
    with managed_session(db):
        advance_user_identity_version(current_user)
        db.flush()
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action='auth.sessions_revoked',
            target_type='user',
            target_id=current_user.id,
            old_value=None,
            new_value={'all_sessions_revoked': True},
        )
        db.flush()
    return {'ok': True}


@router.get('/mfa/status', response_model=MfaStatusRead)
def read_mfa_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return MfaStatusRead(**mfa_status_payload(ensure_credential_policy(db, current_user.id)))


@router.post('/mfa/setup/begin', response_model=MfaSetupBeginRead)
def start_mfa_setup(
    payload: MfaSetupBeginRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _verify_password_or_400(current_user, payload.current_password)
    policy = ensure_credential_policy(db, current_user.id)
    if policy.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='MFA is already enabled')
    try:
        with managed_session(db):
            _policy, secret, otpauth_uri = begin_mfa_setup(db, current_user)
            log_admin_audit(
                db,
                actor_id=current_user.id,
                action='auth.mfa_setup_started',
                target_type='user',
                target_id=current_user.id,
                old_value=None,
                new_value={'setup_pending': True},
            )
            db.flush()
    except RuntimeError as exc:
        raise _service_unavailable(exc) from exc
    return MfaSetupBeginRead(secret=secret, otpauth_uri=otpauth_uri)


@router.post('/mfa/setup/cancel', response_model=MfaActionRead)
def stop_mfa_setup(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    with managed_session(db):
        cancel_mfa_setup(db, current_user.id)
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action='auth.mfa_setup_cancelled',
            target_type='user',
            target_id=current_user.id,
            old_value={'setup_pending': True},
            new_value={'setup_pending': False},
        )
        db.flush()
    return MfaActionRead(ok=True, reauthenticate=False)


@router.post('/mfa/setup/confirm', response_model=MfaRecoveryCodesRead)
def finish_mfa_setup(
    payload: MfaSetupConfirmRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        with managed_session(db):
            policy, recovery_codes = confirm_mfa_setup(db, current_user.id, payload.code)
            advance_user_identity_version(current_user)
            db.flush()
            log_admin_audit(
                db,
                actor_id=current_user.id,
                action='auth.mfa_enabled',
                target_type='user',
                target_id=current_user.id,
                old_value={'mfa_enabled': False},
                new_value={
                    'mfa_enabled': True,
                    'recovery_codes_remaining': mfa_status_payload(policy)['recovery_codes_remaining'],
                    'reauthentication_required': True,
                },
            )
            db.flush()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise _service_unavailable(exc) from exc
    return MfaRecoveryCodesRead(recovery_codes=recovery_codes, reauthenticate=True)


@router.post('/mfa/recovery-codes/regenerate', response_model=MfaRecoveryCodesRead)
def replace_mfa_recovery_codes(
    payload: MfaSensitiveActionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        with managed_session(db):
            policy, method = _verify_sensitive_mfa_action(db, current_user, payload)
            recovery_codes = regenerate_recovery_codes(db, policy)
            advance_user_identity_version(current_user)
            db.flush()
            log_admin_audit(
                db,
                actor_id=current_user.id,
                action='auth.mfa_recovery_codes_regenerated',
                target_type='user',
                target_id=current_user.id,
                old_value=None,
                new_value={
                    'verification_method': method,
                    'recovery_codes_remaining': len(recovery_codes),
                    'reauthentication_required': True,
                },
            )
            db.flush()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return MfaRecoveryCodesRead(recovery_codes=recovery_codes, reauthenticate=True)


@router.post('/mfa/disable', response_model=MfaActionRead)
def disable_mfa(
    payload: MfaSensitiveActionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    with managed_session(db):
        _policy, method = _verify_sensitive_mfa_action(db, current_user, payload)
        clear_mfa(db, current_user.id)
        advance_user_identity_version(current_user)
        db.flush()
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action='auth.mfa_disabled',
            target_type='user',
            target_id=current_user.id,
            old_value={'mfa_enabled': True},
            new_value={
                'mfa_enabled': False,
                'verification_method': method,
                'reauthentication_required': True,
            },
        )
        db.flush()
    return MfaActionRead(ok=True, reauthenticate=True)
