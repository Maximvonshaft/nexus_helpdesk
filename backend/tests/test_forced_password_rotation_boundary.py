from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/forced_password_rotation_boundary_tests.db")
os.environ.setdefault("JWT_SECRET_KEY", "forced-password-rotation-boundary-secret-long-enough")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.deps import get_current_user  # noqa: E402
from app.auth_service import create_access_token, hash_password  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.model_registry import register_all_models  # noqa: E402
from app.models import User  # noqa: E402
from app.models_identity import UserSecurityState  # noqa: E402

register_all_models()


def _request(path: str) -> Request:
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    })


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "forced-rotation.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_forced_rotation_blocks_application_apis_but_allows_recovery_endpoints(db_session):
    user = User(
        username="forced-agent",
        display_name="Forced Agent",
        email="forced-agent@invalid.test",
        password_hash=hash_password("Issued-Password-123!"),
        role=UserRole.agent,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    state = db_session.get(UserSecurityState, user.id)
    assert state is not None
    assert state.must_change_password is True

    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=create_access_token(user.id, session_version=state.session_version),
    )

    with pytest.raises(HTTPException) as exc:
        get_current_user(
            credentials=credentials,
            x_user_id=None,
            db=db_session,
            request=_request("/api/tickets"),
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "Password change required"

    for path in (
        "/api/auth/me",
        "/api/auth/change-password",
        "/api/auth/logout-all",
    ):
        assert get_current_user(
            credentials=credentials,
            x_user_id=None,
            db=db_session,
            request=_request(path),
        ).id == user.id
