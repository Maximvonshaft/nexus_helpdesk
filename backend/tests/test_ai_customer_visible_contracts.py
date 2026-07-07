import os
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/helpdesk_suite_ai_customer_visible_contracts.db")
os.environ.setdefault("KNOWLEDGE_RUNTIME_VERSION", "v2")
os.environ.setdefault("KNOWLEDGE_EMBEDDINGS_ENABLED", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import hash_password  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import ConversationState, MessageStatus, ResolutionCategory, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.models import Team, Ticket, TicketOutboundMessage, User  # noqa: E402
from app.schemas_control_plane import KnowledgeItemCreate, KnowledgePublishRequest  # noqa: E402
from app.services.ai_reply_contract import AI_REPLY_CONTRACT_V2, build_ai_reply_contract  # noqa: E402
from app.services.knowledge_runtime_v2 import retrieve_knowledge  # noqa: E402
from app.services import knowledge_service  # noqa: E402
from app.services.message_dispatch import process_outbound_message, queue_outbound_message  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "ai_customer_visible_contracts.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _user(db_session) -> User:
    team = Team(name=f"Ops-{_uid()}", team_type="support")
    user = User(
        username=f"agent-{_uid()}",
        display_name="Agent",
        email=f"agent-{_uid()}@example.com",
        password_hash=hash_password("pass123"),
        role=UserRole.admin,
        team_id=team.id,
        is_active=True,
    )
    db_session.add_all([team, user])
    db_session.flush()
    return user


def _ticket(db_session, *, state: ConversationState = ConversationState.ai_active) -> Ticket:
    team = Team(name=f"Support-{_uid()}", team_type="support")
    ticket = Ticket(
        ticket_no=f"T-{_uid()}",
        title="Customer message",
        description="Customer message",
        source=TicketSource.user_message,
        source_channel=SourceChannel.whatsapp,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        resolution_category=ResolutionCategory.none,
        team_id=team.id,
        source_chat_id="+15550001",
        preferred_reply_channel=SourceChannel.whatsapp.value,
        preferred_reply_contact="+15550001",
        conversation_state=state,
    )
    db_session.add_all([team, ticket])
    db_session.flush()
    return ticket


def _publish(db_session, actor, **overrides):
    data = {
        "item_key": f"kb.{_uid()}",
        "title": "Knowledge",
        "summary": "Knowledge",
        "status": "draft",
        "source_type": "text",
        "knowledge_kind": "faq",
        "tenant_id": "default",
        "brand_id": "default",
        "country_scope": "GLOBAL",
        "channel_scope": "website",
        "locale": "en",
        "visibility": "customer",
        "shareability": "customer_visible",
        "authority_level": "faq",
        "risk_level": "low",
        "audience_scope": "customer",
        "language": "en",
        "priority": 100,
        "fact_question": "How long does delivery take?",
        "fact_answer": "Global delivery normally takes 3-5 working days.",
        "fact_status": "approved",
        "answer_mode": "direct_answer",
        "draft_body": "How long does delivery take?\nGlobal delivery normally takes 3-5 working days.",
    }
    data.update(overrides)
    item = knowledge_service.create_item(db_session, KnowledgeItemCreate(**data), actor)
    knowledge_service.publish_item(db_session, item, actor, notes=KnowledgePublishRequest().notes)
    db_session.flush()
    return item


def test_ai_visible_reply_requires_runtime_trace(db_session):
    ticket = _ticket(db_session)

    with pytest.raises(ValueError, match="runtime_trace_id_required"):
        queue_outbound_message(
            db_session,
            ticket_id=ticket.id,
            channel=SourceChannel.whatsapp,
            body="AI generated reply",
            created_by=None,
            origin="provider_runtime",
            runtime_contract_version=AI_REPLY_CONTRACT_V2,
            runtime_signature="missing",
            safety_status="passed",
        )


def test_business_system_cannot_queue_customer_visible_ai_text(db_session):
    ticket = _ticket(db_session)

    with pytest.raises(ValueError, match="business_system_cannot_queue_customer_visible_text"):
        queue_outbound_message(
            db_session,
            ticket_id=ticket.id,
            channel=SourceChannel.whatsapp,
            body="Business system fallback text",
            created_by=None,
            origin="business_system",
        )


def test_tool_service_cannot_return_customer_visible_text(db_session):
    ticket = _ticket(db_session)

    with pytest.raises(ValueError, match="tool_service_cannot_queue_customer_visible_text"):
        queue_outbound_message(
            db_session,
            ticket_id=ticket.id,
            channel=SourceChannel.whatsapp,
            body="Tool-created visible text",
            created_by=None,
            origin="tool_service",
        )


def test_human_active_blocks_ai_autoreply(db_session):
    ticket = _ticket(db_session, state=ConversationState.human_owned)
    contract = build_ai_reply_contract(body="AI reply", runtime_trace={"request_id": "rt-human-active"})

    with pytest.raises(ValueError, match="human_active_blocks_ai_autoreply"):
        queue_outbound_message(
            db_session,
            ticket_id=ticket.id,
            channel=SourceChannel.whatsapp,
            body="AI reply",
            created_by=None,
            origin="provider_runtime",
            runtime_trace_id=contract.runtime_trace_id,
            runtime_contract_version=contract.contract_version,
            runtime_signature=contract.runtime_signature,
            safety_status=contract.safety_status,
        )


def test_country_scope_specific_beats_global(db_session):
    actor = _user(db_session)
    _publish(db_session, actor, item_key=f"kb.global.{_uid()}", country_scope="GLOBAL", fact_answer="Global delivery normally takes 3-5 working days.", draft_body="How long does delivery take?\nGlobal delivery normally takes 3-5 working days.")
    _publish(db_session, actor, item_key=f"kb.ch.{_uid()}", country_scope="CH", fact_answer="Switzerland delivery normally takes 1-2 working days.", draft_body="How long does delivery take?\nSwitzerland delivery normally takes 1-2 working days.")

    result = retrieve_knowledge(db_session, query="How long does delivery take?", tenant_key="default", brand_id="default", country_scope="CH", channel_scope="website", channel="website", language="en", limit=5)

    assert result.hits
    assert result.hits[0].metadata["country_scope"] == "CH"
    assert "Switzerland" in (result.hits[0].direct_answer or result.hits[0].text)


def test_internal_note_never_customer_quoted(db_session):
    actor = _user(db_session)
    _publish(
        db_session,
        actor,
        item_key=f"kb.internal.{_uid()}",
        visibility="internal",
        shareability="internal_only",
        fact_question="What is the escalation password?",
        fact_answer="Internal note secret phrase should never be quoted.",
        draft_body="Internal note secret phrase should never be quoted.",
    )

    result = retrieve_knowledge(db_session, query="Internal note secret phrase", tenant_key="default", brand_id="default", country_scope="GLOBAL", channel_scope="website", channel="website", language="en", limit=5)

    assert result.hits == []
    assert result.no_answer_reason == "no_evidence"


def test_low_score_policy_query_returns_no_answer(db_session):
    actor = _user(db_session)
    _publish(
        db_session,
        actor,
        item_key=f"kb.refund.faq.{_uid()}",
        authority_level="faq",
        risk_level="high",
        fact_question="Can I get a refund?",
        fact_answer="Refunds are reviewed by support.",
        draft_body="Can I get a refund?\nRefunds are reviewed by support.",
    )

    result = retrieve_knowledge(db_session, query="refund", tenant_key="default", brand_id="default", country_scope="GLOBAL", channel_scope="website", channel="website", language="en", limit=5)

    assert result.hits == []
    assert result.no_answer_reason == "no_evidence"


def test_knowledge_retrieval_never_crosses_tenant(db_session):
    actor = _user(db_session)
    _publish(db_session, actor, item_key=f"kb.tenant.a.{_uid()}", tenant_id="tenant-a", fact_answer="Tenant A answer.", draft_body="Tenant scoped answer\nTenant A answer.")
    _publish(db_session, actor, item_key=f"kb.tenant.b.{_uid()}", tenant_id="tenant-b", fact_answer="Tenant B answer.", draft_body="Tenant scoped answer\nTenant B answer.")

    result = retrieve_knowledge(db_session, query="Tenant scoped answer", tenant_key="tenant-a", brand_id="default", country_scope="GLOBAL", channel_scope="website", channel="website", language="en", limit=5)

    assert result.hits
    assert {hit.metadata["tenant_id"] for hit in result.hits} == {"tenant-a"}
    assert all("Tenant B" not in (hit.direct_answer or hit.text) for hit in result.hits)


def test_process_outbound_message_rechecks_runtime_contract(db_session, monkeypatch):
    ticket = _ticket(db_session)
    row = TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=SourceChannel.whatsapp,
        status=MessageStatus.pending,
        body="AI reply",
        origin="provider_runtime",
        runtime_trace_id="rt-1",
        runtime_contract_version=AI_REPLY_CONTRACT_V2,
        runtime_signature="bad-signature",
        safety_status="passed",
        provider_status="queued",
        max_retries=1,
    )
    db_session.add(row)
    db_session.flush()
    monkeypatch.setattr("app.services.message_dispatch.dispatch_whatsapp_native_outbound", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dispatch must not run")))

    processed = process_outbound_message(db_session, row)

    assert processed.status == MessageStatus.dead
    assert processed.failure_code == "runtime_signature_invalid"
