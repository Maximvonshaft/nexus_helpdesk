#!/usr/bin/env python3
"""Seed only bounded synthetic identities required by the isolated RC profile."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import func

_BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
if not _BACKEND_ROOT.is_dir():
    raise SystemExit("RC_SEED_FAILED reason=backend_root_missing")
_backend_text = str(_BACKEND_ROOT)
if _backend_text not in sys.path:
    sys.path.insert(0, _backend_text)

from app.auth_service import hash_password
from app.db import SessionLocal
from app.enums import UserRole
from app.model_registry import register_all_models
from app.models import Tenant, User
from app.models_webchat_binding import WebchatPublicOriginBinding
from app.operator_models import OperatorQueueScopeGrant
from app.services.tenant_authority import (
    RUNTIME_TENANT_ASSIGNMENT_SOURCE,
    RUNTIME_TENANT_ASSIGNMENT_VERSION,
)
from app.services.webchat_tenant_binding import normalize_country_code, normalize_public_origin

DEFAULT_ORIGIN = "https://rc-test.invalid"
DEFAULT_TENANT_KEY = "rc-test"
DEFAULT_COUNTRY_CODE = "CH"
DEFAULT_CHANNEL_KEY = "website"
DEFAULT_DISPLAY_NAME = "RC-Test-Website"
DEFAULT_ADMIN_USERNAME = "rc_admin"


def _bounded_env(name: str, default: str, *, max_length: int) -> str:
    value = str(os.getenv(name, default)).strip()
    if not value or len(value) > max_length or any(char in value for char in "\r\n\x00"):
        raise ValueError(f"invalid {name}")
    return value


def seed_rc_authorities() -> WebchatPublicOriginBinding:
    register_all_models()

    requested_origin = _bounded_env("RC_PUBLIC_ORIGIN", DEFAULT_ORIGIN, max_length=255)
    origin = normalize_public_origin(requested_origin)
    if origin is None:
        raise ValueError("invalid RC_PUBLIC_ORIGIN")
    tenant_key = _bounded_env("RC_TEST_TENANT_KEY", DEFAULT_TENANT_KEY, max_length=80)
    country_code = normalize_country_code(
        _bounded_env("RC_TEST_COUNTRY_CODE", DEFAULT_COUNTRY_CODE, max_length=8)
    )
    if country_code is None:
        raise ValueError("invalid RC_TEST_COUNTRY_CODE")
    channel_key = _bounded_env("RC_TEST_CHANNEL_KEY", DEFAULT_CHANNEL_KEY, max_length=120)
    display_name = _bounded_env("RC_TEST_DISPLAY_NAME", DEFAULT_DISPLAY_NAME, max_length=160)
    username = _bounded_env("RC_TEST_ADMIN_USERNAME", DEFAULT_ADMIN_USERNAME, max_length=80)
    password = _bounded_env("RC_TEST_ADMIN_PASSWORD", "rc-test-password-unset", max_length=256)

    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.tenant_key == tenant_key).first()
        if tenant is None:
            tenant = Tenant(
                tenant_key=tenant_key,
                display_name=display_name,
                is_active=True,
            )
            db.add(tenant)
            db.flush()
        else:
            tenant.display_name = display_name
            tenant.is_active = True

        binding = (
            db.query(WebchatPublicOriginBinding)
            .filter(WebchatPublicOriginBinding.normalized_origin == origin)
            .first()
        )
        if binding is None:
            binding = WebchatPublicOriginBinding(
                normalized_origin=origin,
                tenant_key=tenant_key,
                country_code=country_code,
                channel_key=channel_key,
                display_name=display_name,
                is_active=True,
                created_by=None,
                updated_by=None,
            )
            db.add(binding)
        else:
            binding.tenant_key = tenant_key
            binding.country_code = country_code
            binding.channel_key = channel_key
            binding.display_name = display_name
            binding.is_active = True
            binding.updated_by = None

        user = db.query(User).filter(func.lower(User.username) == username.lower()).first()
        if user is None:
            user = User(
                username=username,
                display_name="RC Test Administrator",
                email=None,
                password_hash=hash_password(password),
                role=UserRole.admin,
                is_active=True,
            )
            db.add(user)
        else:
            user.password_hash = hash_password(password)
            user.role = UserRole.admin
            user.is_active = True
        user.tenant_id = tenant.id
        user.tenant_assignment_source = RUNTIME_TENANT_ASSIGNMENT_SOURCE
        user.tenant_assignment_version = RUNTIME_TENANT_ASSIGNMENT_VERSION
        db.flush()

        grant = (
            db.query(OperatorQueueScopeGrant)
            .filter(
                OperatorQueueScopeGrant.user_id == user.id,
                OperatorQueueScopeGrant.tenant_key == tenant_key,
                OperatorQueueScopeGrant.country_code == country_code,
                OperatorQueueScopeGrant.channel_key == channel_key,
            )
            .first()
        )
        if grant is None:
            grant = OperatorQueueScopeGrant(
                user_id=user.id,
                tenant_key=tenant_key,
                country_code=country_code,
                channel_key=channel_key,
                enabled=True,
                granted_by=user.id,
            )
            db.add(grant)
        else:
            grant.enabled = True
            grant.granted_by = user.id

        db.commit()
        db.refresh(binding)
        return binding
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    try:
        seeded = seed_rc_authorities()
    except ValueError:
        print("RC_SEED_FAILED reason=invalid_configuration")
        return 2
    except Exception:
        print("RC_SEED_FAILED reason=database_or_model_boundary")
        return 2
    print(f"RC_TEST_AUTHORITIES_READY=true binding_id={seeded.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
