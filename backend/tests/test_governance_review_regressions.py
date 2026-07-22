from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.governance import _find_visible_knowledge_import_duplicate
from app.db import Base
from app.models_control_plane import KnowledgeItem
from app.models_governance import KnowledgeImportBatch, KnowledgeImportDocument


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'governance_review_regressions.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )
    Base.metadata.create_all(engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_knowledge_import_duplicate_detection_is_scoped_to_current_item_visibility(db_session):
    batch = KnowledgeImportBatch(
        tenant_id="tenant-a",
        status="ready",
        total_files=1,
        succeeded_files=1,
        market_id=101,
        channel="webchat",
        audience_scope="customer",
        language="en",
    )
    item = KnowledgeItem(
        item_key="review-scope-item",
        title="Scoped document",
        tenant_id="tenant-a",
        market_id=101,
        channel="webchat",
        audience_scope="customer",
        language="en",
        status="draft",
    )
    db_session.add_all([batch, item])
    db_session.flush()
    document = KnowledgeImportDocument(
        batch_id=batch.id,
        tenant_id="tenant-a",
        position=1,
        original_file_name="policy.pdf",
        sha256="a" * 64,
        status="draft_created",
        knowledge_item_id=item.id,
    )
    db_session.add(document)
    db_session.commit()

    duplicate = _find_visible_knowledge_import_duplicate(
        db_session,
        tenant_id="tenant-a",
        sha256="a" * 64,
        market_id=101,
        channel="webchat",
        audience_scope="customer",
        language="en",
    )
    assert duplicate is not None
    assert duplicate.id == document.id

    mismatched_scopes = (
        {"market_id": 202},
        {"channel": "email"},
        {"audience_scope": "internal"},
        {"language": "de"},
    )
    for override in mismatched_scopes:
        scope = {
            "market_id": 101,
            "channel": "webchat",
            "audience_scope": "customer",
            "language": "en",
            **override,
        }
        assert (
            _find_visible_knowledge_import_duplicate(
                db_session,
                tenant_id="tenant-a",
                sha256="a" * 64,
                **scope,
            )
            is None
        )

    item.channel = "email"
    db_session.commit()
    assert (
        _find_visible_knowledge_import_duplicate(
            db_session,
            tenant_id="tenant-a",
            sha256="a" * 64,
            market_id=101,
            channel="webchat",
            audience_scope="customer",
            language="en",
        )
        is None
    )
