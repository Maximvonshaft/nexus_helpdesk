from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import knowledge_items
from app.db import Base
from app.models_control_plane import KnowledgeItem
from app.schemas_control_plane import KnowledgeConflictCheckRequest
from app.services import knowledge_service
from app.services.knowledge_studio_service import run_conflict_check


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'knowledge_tenant_authority.db'}",
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


def _item(*, key: str, tenant: str, question: str, priority: int = 100) -> KnowledgeItem:
    return KnowledgeItem(
        item_key=key,
        title=key,
        tenant_id=tenant,
        status="active",
        source_type="text",
        knowledge_kind="faq",
        visibility="customer",
        shareability="customer_visible",
        fact_question=question,
        fact_answer=f"answer for {tenant}",
        fact_status="approved",
        answer_mode="direct_answer",
        audience_scope="customer",
        published_version=1,
        published_normalized_text=question,
        priority=priority,
    )


def test_canonical_knowledge_reads_search_and_conflicts_are_tenant_scoped(db_session):
    tenant_a_one = _item(key="tenant-a-one", tenant="tenant-a", question="same question")
    tenant_a_two = _item(
        key="tenant-a-two", tenant="tenant-a", question="same question", priority=101
    )
    tenant_b = _item(key="tenant-b", tenant="tenant-b", question="same question")
    db_session.add_all([tenant_a_one, tenant_a_two, tenant_b])
    db_session.commit()

    rows, total = knowledge_service.list_items(
        db_session, tenant_id="tenant-a", limit=20
    )
    assert total == 2
    assert {row.item_key for row in rows} == {"tenant-a-one", "tenant-a-two"}

    assert knowledge_service.get_item_or_404(
        db_session, tenant_a_one.id, tenant_id="tenant-a"
    ).id == tenant_a_one.id
    with pytest.raises(HTTPException) as hidden:
        knowledge_service.get_item_or_404(
            db_session, tenant_b.id, tenant_id="tenant-a"
        )
    assert hidden.value.status_code == 404

    published, published_total = knowledge_service.search_published(
        db_session, tenant_id="tenant-a", q="same", limit=20
    )
    assert published_total == 2
    assert {row.item_key for row in published} == {"tenant-a-one", "tenant-a-two"}

    conflicts = run_conflict_check(
        db_session, KnowledgeConflictCheckRequest(limit=20), tenant_id="tenant-a"
    )
    assert conflicts["total"] == 1
    assert set(conflicts["conflicts"][0]["item_ids"]) == {
        tenant_a_one.id, tenant_a_two.id
    }
    assert tenant_b.id not in conflicts["conflicts"][0]["item_ids"]


def test_knowledge_update_cannot_move_item_between_tenants(db_session):
    item = _item(key="tenant-update", tenant="tenant-a", question="question")
    db_session.add(item)
    db_session.commit()
    payload = SimpleNamespace(
        model_dump=lambda **_: {"tenant_id": "tenant-b", "title": "Updated"}
    )
    knowledge_service.update_item(
        db_session, item, payload, actor=None, tenant_id="tenant-a"
    )
    assert item.tenant_id == "tenant-a"
    assert item.title == "Updated"
    with pytest.raises(HTTPException) as hidden:
        knowledge_service.update_item(
            db_session, item, payload, actor=None, tenant_id="tenant-b"
        )
    assert hidden.value.status_code == 404


def test_canonical_knowledge_api_uses_authenticated_tenant_authority():
    source = inspect.getsource(knowledge_items)
    assert "authoritative_tenant_key" in source
    assert source.count("tenant_id=tenant_key") >= 9
    assert "requested=payload.tenant_key" in source
