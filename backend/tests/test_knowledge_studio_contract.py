from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models as _models  # noqa: E402,F401
from app import models_control_plane as _models_control_plane  # noqa: E402,F401
from app.auth_service import create_access_token  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.schemas_control_plane import KnowledgeItemCreate  # noqa: E402
from app.services import knowledge_service  # noqa: E402


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def _user(db_session, *, role: UserRole, suffix: str = "") -> User:
    row = User(
        username=f"{role.value}_knowledge_studio{suffix}",
        display_name=f"{role.value.title()} Knowledge Studio",
        email=f"{role.value}.knowledge_studio{suffix}@example.test",
        password_hash="x",
        role=role,
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _knowledge_payload(**overrides) -> KnowledgeItemCreate:
    data = {
        "item_key": "knowledge.studio.address.unique",
        "title": "Address Change Policy",
        "summary": "Customers can change address before dispatch",
        "status": "draft",
        "source_type": "text",
        "knowledge_kind": "business_fact",
        "channel": "webchat",
        "audience_scope": "customer",
        "language": "en",
        "priority": 10,
        "fact_question": "Can I change delivery address?",
        "fact_answer": "Customers can change delivery address before dispatch.",
        "fact_aliases_json": ["address change", "change delivery address"],
        "fact_status": "approved",
        "answer_mode": "direct_answer",
        "draft_body": "Customers can change delivery address before dispatch.",
        "draft_normalized_text": "customers can change delivery address before dispatch",
    }
    data.update(overrides)
    return KnowledgeItemCreate(**data)


def _seed_knowledge_studio(db_session):
    admin = _user(db_session, role=UserRole.admin)
    agent = _user(db_session, role=UserRole.agent, suffix="_agent")
    published = knowledge_service.create_item(db_session, _knowledge_payload(item_key="knowledge.studio.address.published"), admin)
    knowledge_service.publish_item(db_session, published, admin, notes="publish address policy")
    conflict_draft = knowledge_service.create_item(
        db_session,
        _knowledge_payload(
            item_key="knowledge.studio.address.conflict",
            title="Address Change Conflict Draft",
            priority=20,
            fact_answer="Address changes require manual verification after dispatch.",
            draft_body="Address changes require manual verification after dispatch.",
            draft_normalized_text="address changes require manual verification after dispatch",
        ),
        admin,
    )
    document = knowledge_service.create_item(
        db_session,
        _knowledge_payload(
            item_key="knowledge.studio.pod.document",
            title="POD Document",
            source_type="file",
            knowledge_kind="document",
            priority=30,
            fact_question=None,
            fact_answer=None,
            fact_aliases_json=[],
            fact_status="draft",
            answer_mode="guided_answer",
            draft_body="POD means proof of delivery. It confirms the delivery recipient and timestamp.",
            draft_normalized_text="pod means proof of delivery confirms recipient timestamp",
        ),
        admin,
    )
    document.parsing_status = "parsed"
    document.file_name = "pod-policy.txt"
    document.file_storage_key = "knowledge/pod-policy.txt"
    knowledge_service.publish_item(db_session, document, admin, notes="publish pod document")
    db_session.flush()
    return admin, agent, published, conflict_draft, document


def test_knowledge_studio_contract_uses_real_knowledge_tables_and_retrieval(tmp_path):
    db_file = tmp_path / "knowledge_studio.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    db_session = TestingSession()
    admin, _agent, published, conflict_draft, document = _seed_knowledge_studio(db_session)
    db_session.commit()

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        response = client.get("/api/lite/knowledge-studio", headers=_headers(admin))
        retrieval = client.post(
            "/api/knowledge-items/retrieve-test",
            headers=_headers(admin),
            json={"q": "Can I change delivery address?", "channel": "webchat", "audience_scope": "customer", "language": "en", "limit": 5},
        )
        conflict_check = client.post(
            "/api/knowledge-items/conflict-check",
            headers=_headers(admin),
            json={"q": "address change", "channel": "webchat", "audience_scope": "customer", "language": "en", "limit": 12},
        )
        golden_test = client.post(
            "/api/knowledge-items/golden-test",
            headers=_headers(admin),
            json={
                "q": "Can I change delivery address?",
                "channel": "webchat",
                "audience_scope": "customer",
                "language": "en",
                "expected_item_key": published.item_key,
                "expected_answer_contains": "before dispatch",
                "forbidden_answer_terms": ["manual verification after dispatch"],
                "min_score": 1,
                "limit": 5,
            },
        )
    finally:
        app.dependency_overrides.pop(get_db, None)
        db_session.close()
        Base.metadata.drop_all(engine)

    assert response.status_code == 200, response.text
    payload = response.json()
    kpis = {item["key"]: item for item in payload["kpis"]}
    items = {item["item_key"]: item for item in payload["items"]}
    blocks = {item["key"]: item for item in payload["template_blocks"]}
    lifecycle = {item["key"]: item for item in payload["release_lifecycle"]}

    assert payload["role"] == "admin"
    assert "ai_config.manage" in payload["capabilities"]
    assert kpis["total_items"]["value"] == 3
    assert kpis["active_published"]["value"] == 2
    assert kpis["indexed_chunks"]["value"] >= 2
    assert kpis["conflict_groups"]["value"] >= 1
    assert items[published.item_key]["retrieval_test_ready"] is True
    assert items[conflict_draft.item_key]["has_conflict"] is True
    assert items[conflict_draft.item_key]["publish_ready"] is False
    assert items[document.item_key]["source_type"] == "file"
    assert blocks["asset-library"]["status"] == "implemented"
    assert blocks["retrieval-test"]["backend_contract"] == "POST /api/knowledge-items/retrieve-test"
    assert blocks["conflict-scan"]["status"] == "implemented"
    assert blocks["golden-test"]["status"] == "implemented"
    assert lifecycle["conflict-scan"]["status"] == "implemented"
    assert lifecycle["golden-test"]["status"] == "implemented"
    assert lifecycle["rollback"]["count"] >= 2
    assert payload["facts"]["dedicated_conflict_check_endpoint"] == "implemented"
    assert payload["facts"]["dedicated_golden_test_endpoint"] == "implemented"

    assert retrieval.status_code == 200, retrieval.text
    retrieval_payload = retrieval.json()
    assert retrieval_payload["total"] >= 1
    assert any(hit["item_key"] == published.item_key for hit in retrieval_payload["hits"])
    assert retrieval_payload["grounding_would_apply"] is True

    assert conflict_check.status_code == 200, conflict_check.text
    conflict_payload = conflict_check.json()
    assert conflict_payload["total"] >= 1
    assert any(
        {published.item_key, conflict_draft.item_key}.issubset(set(item["item_keys"]))
        for item in conflict_payload["conflicts"]
    )
    assert all("evidence" in item for item in conflict_payload["conflicts"])

    assert golden_test.status_code == 200, golden_test.text
    golden_payload = golden_test.json()
    assert golden_payload["passed"] is True
    assertions = {item["key"]: item for item in golden_payload["assertions"]}
    assert assertions["expected-source"]["passed"] is True
    assert assertions["expected-answer"]["passed"] is True
    assert assertions["forbidden-answer"]["passed"] is True
    assert golden_payload["retrieval"]["total"] >= 1


def test_knowledge_studio_requires_ai_config_capability(tmp_path):
    db_file = tmp_path / "knowledge_studio_forbidden.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    db_session = TestingSession()
    _admin, agent, _published, _conflict_draft, _document = _seed_knowledge_studio(db_session)
    db_session.commit()

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        response = client.get("/api/lite/knowledge-studio", headers=_headers(agent))
    finally:
        app.dependency_overrides.pop(get_db, None)
        db_session.close()
        Base.metadata.drop_all(engine)

    assert response.status_code == 403
    assert response.json()["detail"] == "knowledge_studio_requires_ai_config_capability"
