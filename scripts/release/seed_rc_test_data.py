#!/usr/bin/env python3
"""Seed only bounded synthetic identities required by the isolated RC profile."""

from __future__ import annotations

from app.db import SessionLocal
from app.models_webchat_binding import WebchatPublicOriginBinding

ORIGIN = "https://rc-test.invalid"
TENANT_KEY = "rc-test"
CHANNEL_KEY = "website"
DISPLAY_NAME = "RC Test Website"


def seed_public_origin_binding() -> None:
    db = SessionLocal()
    try:
        binding = (
            db.query(WebchatPublicOriginBinding)
            .filter(WebchatPublicOriginBinding.normalized_origin == ORIGIN)
            .first()
        )
        if binding is None:
            binding = WebchatPublicOriginBinding(
                normalized_origin=ORIGIN,
                tenant_key=TENANT_KEY,
                channel_key=CHANNEL_KEY,
                display_name=DISPLAY_NAME,
                is_active=True,
                created_by=None,
                updated_by=None,
            )
            db.add(binding)
        else:
            binding.tenant_key = TENANT_KEY
            binding.channel_key = CHANNEL_KEY
            binding.display_name = DISPLAY_NAME
            binding.is_active = True
            binding.updated_by = None
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_public_origin_binding()
    print("RC_TEST_PUBLIC_ORIGIN_BINDING_READY=true")
