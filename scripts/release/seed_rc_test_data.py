#!/usr/bin/env python3
"""Seed only bounded synthetic identities required by the isolated RC profile."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# The release helpers are copied to /app/scripts while the application package
# lives under /app/backend. Absolute script execution sets sys.path[0] to the
# script directory, so bootstrap the canonical backend root before importing app.
_BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
if not _BACKEND_ROOT.is_dir():
    raise SystemExit("RC_SEED_FAILED reason=backend_root_missing")
_backend_text = str(_BACKEND_ROOT)
if _backend_text not in sys.path:
    sys.path.insert(0, _backend_text)

from app.db import SessionLocal
from app.model_registry import register_all_models
from app.models import Tenant
from app.models_webchat_binding import WebchatPublicOriginBinding
from app.services.webchat_tenant_binding import normalize_public_origin

DEFAULT_ORIGIN = "https://rc-test.invalid"
DEFAULT_TENANT_KEY = "rc-test"
DEFAULT_CHANNEL_KEY = "website"
DEFAULT_DISPLAY_NAME = "RC-Test-Website"


def _bounded_env(name: str, default: str, *, max_length: int) -> str:
    value = str(os.getenv(name, default)).strip()
    if not value or len(value) > max_length or any(char in value for char in "\r\n\x00"):
        raise ValueError(f"invalid {name}")
    return value


def seed_public_origin_binding() -> WebchatPublicOriginBinding:
    # This script runs as a standalone process. Register the complete canonical
    # model set before opening/flushing a Session so foreign-key targets such as
    # users.id are present in SQLAlchemy metadata.
    register_all_models()

    requested_origin = _bounded_env("RC_PUBLIC_ORIGIN", DEFAULT_ORIGIN, max_length=255)
    origin = normalize_public_origin(requested_origin)
    if origin is None:
        raise ValueError("invalid RC_PUBLIC_ORIGIN")
    tenant_key = _bounded_env("RC_TEST_TENANT_KEY", DEFAULT_TENANT_KEY, max_length=80)
    channel_key = _bounded_env("RC_TEST_CHANNEL_KEY", DEFAULT_CHANNEL_KEY, max_length=120)
    display_name = _bounded_env("RC_TEST_DISPLAY_NAME", DEFAULT_DISPLAY_NAME, max_length=160)

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
                channel_key=channel_key,
                display_name=display_name,
                is_active=True,
                created_by=None,
                updated_by=None,
            )
            db.add(binding)
        else:
            binding.tenant_key = tenant_key
            binding.channel_key = channel_key
            binding.display_name = display_name
            binding.is_active = True
            binding.updated_by = None
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
        seeded = seed_public_origin_binding()
    except ValueError:
        print("RC_SEED_FAILED reason=invalid_configuration")
        return 2
    except Exception:
        print("RC_SEED_FAILED reason=database_or_model_boundary")
        return 2
    print(f"RC_TEST_PUBLIC_ORIGIN_BINDING_READY=true id={seeded.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
