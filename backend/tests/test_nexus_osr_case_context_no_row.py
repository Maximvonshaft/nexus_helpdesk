from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models as _models  # noqa: F401
from app import models_osr as _models_osr  # noqa: F401
from app import webchat_models as _webchat_models  # noqa: F401
from app.db import Base
from app.services.nexus_osr.persistence import load_case_context


def test_missing_case_context_returns_none_without_tenant_evidence(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'missing-context.db'}", future=True)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()
    try:
        assert load_case_context(db, conversation_id=999999) is None
        assert load_case_context(db, ticket_id=999999) is None
    finally:
        db.close()
        engine.dispose()
