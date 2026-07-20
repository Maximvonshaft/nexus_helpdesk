from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models as _models  # noqa: E402,F401
from app import models_identity_policy as _identity_models  # noqa: E402,F401
from app.api.webchat_ws import _load_user  # noqa: E402
from app.auth_service import (  # noqa: E402
    create_access_token,
    hash_password,
    load_authenticated_user_for_token,
)
from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.models import User  # noqa: E402
from app.models_identity_policy import UserCredentialPolicy  # noqa: E402


def test_forced_rotation_token_is_recovery_only_for_agent_websocket(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'webchat-forced-rotation.db'}",
        connect_args={"check_same_thread": False},
    )
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    db = Session()
    try:
        user = User(
            username="websocket_recovery_agent",
            display_name="WebSocket Recovery Agent",
            email="websocket_recovery_agent@example.test",
            password_hash=hash_password("Nexus!WebSocket2026"),
            role=UserRole.agent,
            is_active=True,
        )
        db.add(user)
        db.flush()
        policy = db.get(UserCredentialPolicy, user.id)
        assert policy is not None
        policy.must_change_password = True
        db.commit()

        token = create_access_token(user.id, user.updated_at)
        assert load_authenticated_user_for_token(db, token) is not None
        assert _load_user(db, token) is None

        policy.must_change_password = False
        db.commit()
        assert _load_user(db, token) is not None
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
