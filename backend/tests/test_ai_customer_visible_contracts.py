import hashlib
import hmac
import json
import os
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/helpdesk_suite_ai_customer_visible_contracts.db")
os.environ.setdefault("KNOWLEDGE_EMBEDDINGS_ENABLED", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import hash_password  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import ConversationState, MessageStatus, ResolutionCategory, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.models import Team, Ticket, TicketOutboundMessage, User  # noqa: E402
from app.schemas_control_plane import KnowledgeItemCreate, KnowledgePublishRequest  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.services.ai_reply_contract import AI_REPLY_CONTRACT, build_ai_reply_contract  # noqa: E402
from app.services.knowledge_runtime import retrieve_knowledge  # noqa: E402
from app.services import knowledge_service  # noqa: E402
from app.services.customer_visible_message_service import create_customer_visible_outbound, record_runtime_null_reply  # noqa: E402
from app.services.message_dispatch import process_outbound_message, queue_outbound_message  # noqa: E402
from app.services.customer_visible_policy import CustomerVisiblePolicyDecision  # noqa: E402


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
            runtime_contract_version=AI_REPLY_CONTRACT,
            runtime_signature="missing",
            safety_status="passed",
        )


def test_retired_ai_reply_contract_is_rejected():
    with pytest.raises(ValueError, match="runtime_contract_version_invalid"):
        build_ai_reply_contract(
            body="Retired contract reply",
            runtime_trace={"request_id": "rt-retired-contract"},
            contract_version="nexus.ai_reply.v2",
            reply_type="clarifying_question",
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
    contract = build_ai_reply_contract(
        body="AI reply",
        runtime_trace={"request_id": "rt-human-active"},
        reply_type="clarifying_question",
    )

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


def test_country_specific_knowledge_beats_global(db_session):
    actor = _user(db_session)
    _publish(db_session, actor, item_key=f"kb.global.address.{_uid()}", country_scope="GLOBAL", fact_question="Can I change delivery address?", fact_answer="Global address changes require support review.", draft_body="Can I change delivery address?\nGlobal address changes require support review.")
    _publish(db_session, actor, item_key=f"kb.ch.address.{_uid()}", country_scope="CH", fact_question="Can I change delivery address?", fact_answer="Switzerland address changes are allowed before dispatch.", draft_body="Can I change delivery address?\nSwitzerland address changes are allowed before dispatch.")

    result = retrieve_knowledge(db_session, query="Can I change delivery address?", tenant_key="default", brand_id="default", country_scope="CH", channel_scope="website", channel="website", language="en", limit=5)

    assert result.hits
    assert result.hits[0].metadata["country_scope"] == "CH"
    assert "Switzerland" in (result.hits[0].direct_answer or result.hits[0].text)


def test_global_fallback_when_country_specific_missing(db_session):
    actor = _user(db_session)
    _publish(db_session, actor, item_key=f"kb.global.pickup.{_uid()}", country_scope="GLOBAL", fact_question="Can I schedule pickup?", fact_answer="Pickup scheduling is handled by support.", draft_body="Can I schedule pickup?\nPickup scheduling is handled by support.")

    result = retrieve_knowledge(db_session, query="Can I schedule pickup?", tenant_key="default", brand_id="default", country_scope="CH", channel_scope="website", channel="website", language="en", limit=5)

    assert result.hits
    assert {hit.metadata["country_scope"] for hit in result.hits} == {"GLOBAL"}
    assert "Pickup scheduling" in (result.hits[0].direct_answer or result.hits[0].text)


def test_country_specific_knowledge_does_not_cross_country(db_session):
    actor = _user(db_session)
    _publish(db_session, actor, item_key=f"kb.de.customs.{_uid()}", country_scope="DE", fact_question="How does customs clearance work?", fact_answer="Germany customs clearance requires German policy review.", draft_body="How does customs clearance work?\nGermany customs clearance requires German policy review.")

    result = retrieve_knowledge(db_session, query="How does customs clearance work?", tenant_key="default", brand_id="default", country_scope="CH", channel_scope="website", channel="website", language="en", limit=5)

    assert result.hits == []
    assert result.no_answer_reason == "no_evidence"


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
    contract = build_ai_reply_contract(
        body="AI reply",
        runtime_trace={"request_id": "rt-1"},
        reply_type="answer",
        used_sources=["knowledge:test-source"],
    )
    payload = contract.payload_dict(body="AI reply", origin="provider_runtime")
    payload["runtime_signature"] = "bad-signature"
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    row = TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=SourceChannel.whatsapp,
        status=MessageStatus.pending,
        body="AI reply",
        origin="provider_runtime",
        runtime_trace_id="rt-1",
        runtime_contract_version=AI_REPLY_CONTRACT,
        runtime_signature="bad-signature",
        runtime_contract_payload_json=payload_json,
        runtime_contract_payload_sha256=hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
        runtime_reply_type="answer",
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


def test_ai_reply_answer_requires_used_sources():
    with pytest.raises(ValueError, match="ai_reply_answer_requires_used_sources"):
        build_ai_reply_contract(
            body="Grounded answer",
            runtime_trace={"request_id": "rt-v3-no-source"},
            contract_version=AI_REPLY_CONTRACT,
            reply_type="answer",
            used_sources=[],
        )


def test_ai_reply_v3_blocks_unsupported_claims():
    with pytest.raises(ValueError, match="ai_reply_unsupported_claims_blocked"):
        build_ai_reply_contract(
            body="Grounded answer",
            runtime_trace={"request_id": "rt-v3-unsupported"},
            contract_version=AI_REPLY_CONTRACT,
            reply_type="answer",
            used_sources=["kb.policy.1#v1:0"],
            unsupported_claims=["delivery takes one day"],
        )


def test_runtime_signature_uses_hmac_secret(monkeypatch):
    secret = "runtime-contract-signing-secret-32-bytes"
    monkeypatch.setenv("RUNTIME_CONTRACT_SIGNING_SECRET", secret)
    get_settings.cache_clear()
    try:
        contract = build_ai_reply_contract(
            body="Grounded answer",
            runtime_trace={"request_id": "rt-v3-hmac"},
            contract_version=AI_REPLY_CONTRACT,
            reply_type="answer",
            used_sources=["kb.policy.1#v1:0"],
            unsupported_claims=[],
            confidence=0.91,
            channel="webchat",
        )
        payload = {
            "body_sha256": hashlib.sha256("Grounded answer".encode("utf-8")).hexdigest(),
            "runtime_trace_id": "rt-v3-hmac",
            "contract_version": AI_REPLY_CONTRACT,
            "safety_status": "passed",
            "reply": {
                "type": "answer",
                "text_sha256": hashlib.sha256("Grounded answer".encode("utf-8")).hexdigest(),
            },
            "grounding": {
                "used_sources": ["kb.policy.1#v1:0"],
                "unsupported_claims": [],
                "conflicts": [],
            },
            "risk": {"confidence": 0.91},
            "channel": "webchat",
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        expected = hmac.new(secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    finally:
        get_settings.cache_clear()

    assert contract.runtime_signature == expected


def test_v3_answer_with_used_sources_passes_outbound_gateway(db_session, monkeypatch):
    ticket = _ticket(db_session)
    contract = build_ai_reply_contract(
        body="Switzerland address changes are allowed before dispatch.",
        runtime_trace={"request_id": "rt-v3-pass"},
        contract_version=AI_REPLY_CONTRACT,
        reply_type="answer",
        used_sources=["knowledge:ch-address-policy"],
        unsupported_claims=[],
        confidence=0.94,
        channel="whatsapp",
    )
    row = queue_outbound_message(
        db_session,
        ticket_id=ticket.id,
        channel=SourceChannel.whatsapp,
        body="Switzerland address changes are allowed before dispatch.",
        created_by=None,
        origin="provider_runtime",
        runtime_trace_id=contract.runtime_trace_id,
        runtime_contract_version=contract.contract_version,
        runtime_signature=contract.runtime_signature,
        runtime_contract_payload_json=contract.payload_json(body="Switzerland address changes are allowed before dispatch.", origin="provider_runtime"),
        runtime_contract_payload_sha256=contract.payload_sha256(body="Switzerland address changes are allowed before dispatch.", origin="provider_runtime"),
        runtime_reply_type=contract.reply_type,
        safety_status=contract.safety_status,
    )
    monkeypatch.setattr("app.services.message_dispatch._external_dispatch_block_reason", lambda: None)
    monkeypatch.setattr("app.services.message_dispatch._enforce_customer_visible_policy", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        "app.services.message_dispatch._dispatch_whatsapp_message",
        lambda *args, **kwargs: (MessageStatus.sent, "whatsapp_native_sent", None, {"adapter": "test", "idempotency_key": "idem"}),
    )

    processed = process_outbound_message(db_session, row)

    assert processed.status == MessageStatus.sent
    assert processed.runtime_reply_type == "answer"


def test_v3_answer_without_used_sources_blocked(db_session):
    ticket = _ticket(db_session)
    with pytest.raises(ValueError, match="ai_reply_answer_requires_used_sources"):
        queue_outbound_message(
            db_session,
            ticket_id=ticket.id,
            channel=SourceChannel.whatsapp,
            body="Ungrounded answer",
            created_by=None,
            origin="provider_runtime",
            runtime_trace_id="rt-v3-no-sources",
            runtime_contract_version=AI_REPLY_CONTRACT,
            runtime_signature="bad",
            runtime_reply_type="answer",
            safety_status="passed",
        )


def test_v3_unsupported_claims_blocked(db_session):
    ticket = _ticket(db_session)
    with pytest.raises(ValueError, match="ai_reply_unsupported_claims_blocked"):
        build_ai_reply_contract(
            body="Unsupported claim",
            runtime_trace={"request_id": "rt-v3-unsupported-gateway"},
            contract_version=AI_REPLY_CONTRACT,
            reply_type="answer",
            used_sources=["knowledge:policy"],
            unsupported_claims=["unsupported delivery promise"],
            channel="whatsapp",
        )
    contract = build_ai_reply_contract(
        body="Unsupported claim",
        runtime_trace={"request_id": "rt-v3-unsupported-gateway-2"},
        contract_version=AI_REPLY_CONTRACT,
        reply_type="answer",
        used_sources=["knowledge:policy"],
        unsupported_claims=[],
        channel="whatsapp",
    )
    payload = contract.payload_dict(body="Unsupported claim", origin="provider_runtime")
    payload["grounding"]["unsupported_claims"] = ["mutated unsupported claim"]
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    with pytest.raises(ValueError, match="ai_reply_unsupported_claims_blocked"):
        queue_outbound_message(
            db_session,
            ticket_id=ticket.id,
            channel=SourceChannel.whatsapp,
            body="Unsupported claim",
            created_by=None,
            origin="provider_runtime",
            runtime_trace_id=contract.runtime_trace_id,
            runtime_contract_version=contract.contract_version,
            runtime_signature=contract.runtime_signature,
            runtime_contract_payload_json=payload_json,
            runtime_contract_payload_sha256=hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
            runtime_reply_type="answer",
            safety_status=contract.safety_status,
        )


def test_v3_answer_with_conflicts_blocked():
    with pytest.raises(ValueError, match="ai_reply_conflicts_blocked"):
        build_ai_reply_contract(
            body="Conflicting answer",
            runtime_trace={"request_id": "rt-v3-conflict"},
            contract_version=AI_REPLY_CONTRACT,
            reply_type="answer",
            used_sources=["knowledge:policy-a", "knowledge:policy-b"],
            unsupported_claims=[],
            conflicts=["policy-a and policy-b disagree"],
            channel="webchat",
        )


def test_signed_ai_outbound_body_cannot_be_mutated_after_signature(db_session, monkeypatch):
    ticket = _ticket(db_session)
    contract = build_ai_reply_contract(
        body="Exact signed body",
        runtime_trace={"request_id": "rt-mutation"},
        reply_type="clarifying_question",
    )
    row = queue_outbound_message(
        db_session,
        ticket_id=ticket.id,
        channel=SourceChannel.whatsapp,
        body="Exact signed body",
        created_by=None,
        origin="provider_runtime",
        runtime_trace_id=contract.runtime_trace_id,
        runtime_contract_version=contract.contract_version,
        runtime_signature=contract.runtime_signature,
        runtime_contract_payload_json=contract.payload_json(body="Exact signed body", origin="provider_runtime"),
        runtime_contract_payload_sha256=contract.payload_sha256(body="Exact signed body", origin="provider_runtime"),
        runtime_reply_type=contract.reply_type,
        safety_status=contract.safety_status,
    )
    monkeypatch.setattr("app.services.message_dispatch._external_dispatch_block_reason", lambda: None)
    monkeypatch.setattr(
        "app.services.message_dispatch.evaluate_customer_visible_policy",
        lambda *args, **kwargs: CustomerVisiblePolicyDecision(True, "allow", [], "Exact signed body "),
    )
    monkeypatch.setattr("app.services.message_dispatch.dispatch_whatsapp_native_outbound", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dispatch must not run")))

    processed = process_outbound_message(db_session, row)

    assert processed.status == MessageStatus.dead
    assert processed.failure_code == "runtime_signed_body_mutation"
    assert processed.body == "Exact signed body"


def test_webchat_ai_reply_uses_customer_visible_message_service():
    source = (ROOT / "app/services/webchat_ai_service.py").read_text(encoding="utf-8")
    assert "create_customer_visible_message" in source
    assert "TicketOutboundMessage(" not in source
    assert "queue_outbound_message" not in source


def test_ai_reply_does_not_update_last_human_update():
    source = (ROOT / "app/services/webchat_ai_service.py").read_text(encoding="utf-8")
    assert "ticket.last_human_update" not in source
    assert "ticket.last_ai_update = final_body" in source


def test_dispatch_has_no_ticket_text_fact_evidence_fallback():
    source = (ROOT / "app/services/message_dispatch.py").read_text(encoding="utf-8")
    assert "_build_fact_evidence" not in source
    assert "ticket_operator_context" not in source


def test_v3_null_reply_not_sent_to_customer(db_session):
    ticket = _ticket(db_session)
    contract = build_ai_reply_contract(
        body=None,
        runtime_trace={"request_id": "rt-null"},
        contract_version=AI_REPLY_CONTRACT,
        reply_type="null_reply",
        channel="webchat",
    )

    result = record_runtime_null_reply(db_session, ticket=ticket, ai_contract=contract)

    assert result.outbound_message is None
    assert result.customer_visible is False
    assert db_session.query(TicketOutboundMessage).filter(TicketOutboundMessage.ticket_id == ticket.id).count() == 0
    send_result = create_customer_visible_outbound(
        db_session,
        ticket=ticket,
        channel=SourceChannel.web_chat,
        body="",
        origin="provider_runtime",
        created_by=None,
        provider_status="runtime_null_reply",
        ai_contract=contract,
    )
    assert send_result.outbound_message is None
    assert db_session.query(TicketOutboundMessage).filter(TicketOutboundMessage.ticket_id == ticket.id).count() == 0


def test_handoff_notice_origin_cannot_bypass_contract(db_session):
    ticket = _ticket(db_session)
    with pytest.raises(ValueError, match="unsupported_customer_visible_origin"):
        create_customer_visible_outbound(
            db_session,
            ticket=ticket,
            channel=SourceChannel.web_chat,
            body="A support agent will review this conversation.",
            origin="handoff_notice",
            created_by=None,
            provider_status="handoff_notice",
            status=MessageStatus.sent,
        )

    contract = build_ai_reply_contract(
        body="A support agent will review this conversation.",
        runtime_trace={"request_id": "rt-handoff-notice"},
        contract_version=AI_REPLY_CONTRACT,
        reply_type="handoff_notice",
        unsupported_claims=[],
        channel="webchat",
    )
    row = create_customer_visible_outbound(
        db_session,
        ticket=ticket,
        channel=SourceChannel.web_chat,
        body="A support agent will review this conversation.",
        origin="provider_runtime",
        created_by=None,
        provider_status="handoff_notice",
        ai_contract=contract,
        status=MessageStatus.sent,
    ).outbound_message
    assert row is not None
    assert row.runtime_contract_version == AI_REPLY_CONTRACT
    assert row.runtime_reply_type == "handoff_notice"


def test_webchat_handoff_ack_does_not_create_customer_visible_text_without_runtime_contract():
    source = (ROOT / "app/services/webchat_service.py").read_text(encoding="utf-8")
    assert "webchat_handoff_ack" not in source
    assert 'origin="handoff_notice"' not in source


def test_webchat_ai_does_not_directly_create_customer_visible_webchat_message():
    source = (ROOT / "app/services/webchat_ai_service.py").read_text(encoding="utf-8")
    assert "create_customer_visible_message" in source
    assert "WebchatMessage(" not in source


def test_webchat_ai_does_not_directly_create_external_ticket_comment():
    source = (ROOT / "app/services/webchat_ai_service.py").read_text(encoding="utf-8")
    assert "TicketComment(" not in source
    assert "visibility=NoteVisibility.external" not in source


def test_admin_reply_uses_customer_visible_message_service_for_visible_entities():
    source = (ROOT / "app/services/webchat_service.py").read_text(encoding="utf-8")
    admin_reply = source.split("def admin_reply(", 1)[1]
    assert "create_customer_visible_message(" in admin_reply
    assert "WebchatMessage(" not in admin_reply
    assert "TicketComment(" not in admin_reply
    assert "TicketEvent(" not in admin_reply


def test_originless_external_outbound_is_blocked_after_contract_cutover(db_session, monkeypatch):
    ticket = _ticket(db_session)
    row = TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=SourceChannel.whatsapp,
        status=MessageStatus.pending,
        body="legacy originless text",
        provider_status="queued",
        max_retries=1,
    )
    db_session.add(row)
    db_session.flush()
    monkeypatch.setattr("app.services.message_dispatch.dispatch_whatsapp_native_outbound", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dispatch must not run")))

    processed = process_outbound_message(db_session, row)

    assert processed.status == MessageStatus.dead
    assert processed.failure_code == "missing_customer_visible_origin_contract"
