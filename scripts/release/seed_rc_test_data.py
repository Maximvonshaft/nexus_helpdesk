#!/usr/bin/env python3
"""Seed only bounded synthetic identities required by the isolated RC profile."""

from __future__ import annotations

import os

from app.db import SessionLocal
from app.model_registry import register_all_models
from app.models_webchat_binding import WebchatPublicOriginBinding
from app.services.webchat_tenant_binding import normalize_public_origin

DEFAULT_ORIGIN = "https://rc-test.invalid"
DEFAULT_TENANT_KEY = "rc-test"
DEFAULT_CHANNEL_KEY = "website"
DEFAULT_DISPLAY_NAME = "RC Test Website"


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
    tenant_key = _bounded_env("RC_TEST_TENANT_KEY", DEFAULT_TENANT_KEY, max_length=120)
    channel_key = _bounded_env("RC_TEST_CHANNEL_KEY", DEFAULT_CHANNEL_KEY, max_length=120)
    display_name = _bounded_env("RC_TEST_DISPLAY_NAME", DEFAULT_DISPLAY_NAME, max_length=160)

    db = SessionLocal()
    try:
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


if __name__ == "__main__":
    seeded = seed_public_origin_binding()
    print(f"RC_TEST_PUBLIC_ORIGIN_BINDING_READY=true id={seeded.id}")
