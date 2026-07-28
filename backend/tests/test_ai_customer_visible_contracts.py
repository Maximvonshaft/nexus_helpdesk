from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/ai-customer-visible-contracts.db",
)
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

from app.db import Base
from app.enums import (
    MessageStatus,
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
)
from app.models import Customer, Ticket, TicketOutboundMessage
from app.services.ai_reply_contract import (
    AI_REPLY_CONTRACT,
    build_ai_reply_contract,
    canonical_contract_payload_json,
    contract_payload_sha256,
)
from app.services.customer_visible_message_service import (
    create_customer_visible_outbound,
    record_runtime_null_reply,
)
from app.services.customer_visible_policy import CustomerVisiblePolicyDecision
from app.services.message_dispatch import (
    process_outbound_message,
    queue_outbound_message,
)
from app.settings import get_settings


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ai-visible.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    Base.metadata.create_all(engine)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _ticket(db_session) -> Ticket:
    customer = Customer(
        name="AI Contract Customer",
        phone="+15550000001",
    )
    db_session.add(customer)
    db_session.flush()
    ticket = Ticket(
        ticket_no=f"AI-CUSTOMER-{customer.id}",
        title="AI customer-visible contract",
        description="AI customer-visible contract",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.whatsapp,
        preferred_reply_channel=SourceChannel.whatsapp.value,
        preferred_reply_contact="+15550000001",
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
    )
    db_session.add(ticket)
    db_session.flush()
    return ticket


def _queue_contract(
    db_session,
    ticket: Ticket,
    *,
    body: str,
    contract,
    payload_json: str | None = None,
    payload_sha: str | None = None,
):
    return queue_outbound_message(
        db_session,
        ticket_id=ticket.id,
        channel=SourceChannel.whatsapp,
        body=body,
        created_by=None,
        origin="provider_runtime",
        runtime_trace_id=contract.runtime_trace_id,
        runtime_contract_version=contract.contract_version,
        runtime_signature=contract.runtime_signature,
        runtime_contract_payload_json=(
            payload_json
            if payload_json is not None
            else contract.payload_json(body=body, origin="provider_runtime")
        ),
        runtime_contract_payload_sha256=(
            payload_sha
            if payload_sha is not None
            else contract.payload_sha256(body=body, origin="provider_runtime")
        ),
        runtime_reply_type=contract.reply_type,
        safety_status=contract.safety_status,
    )


def test_queue_requires_runtime_trace_for_provider_runtime(db_session):
    ticket = _ticket(db_session)
    with pytest.raises(ValueError, match="runtime_trace_id_required"):
        queue_outbound_message(
            db_session,
            ticket_id=ticket.id,
            channel=SourceChannel.whatsapp,
            body="Hello",
            created_by=None,
            origin="provider_runtime",
            runtime_contract_version=AI_REPLY_CONTRACT,
            runtime_signature="signature",
            runtime_reply_type="clarifying_question",
            safety_status="passed",
        )


def test_queue_requires_runtime_contract_for_provider_runtime(db_session):
    ticket = _ticket(db_session)
    with pytest.raises(ValueError, match="runtime_contract_version_invalid"):
        queue_outbound_message(
            db_session,
            ticket_id=ticket.id,
            channel=SourceChannel.whatsapp,
            body="Hello",
            created_by=None,
            origin="provider_runtime",
            runtime_trace_id="runtime-trace",
            runtime_signature="signature",
            runtime_reply_type="clarifying_question",
            safety_status="passed",
        )


def test_queue_requires_runtime_signature_for_provider_runtime(db_session):
    ticket = _ticket(db_session)
    contract = build_ai_reply_contract(
        body="Hello",
        runtime_trace={"request_id": "runtime-trace"},
        reply_type="clarifying_question",
    )
    payload = contract.payload_dict(body="Hello", origin="provider_runtime")
    payload["runtime_signature"] = None
    payload_json = canonical_contract_payload_json(payload)
    with pytest.raises(ValueError, match="runtime_signature_invalid"):
        queue_outbound_message(
            db_session,
            ticket_id=ticket.id,
            channel=SourceChannel.whatsapp,
            body="Hello",
            created_by=None,
            origin="provider_runtime",
            runtime_trace_id=contract.runtime_trace_id,
            runtime_contract_version=contract.contract_version,
            runtime_signature=None,
            runtime_contract_payload_json=payload_json,
            runtime_contract_payload_sha256=contract_payload_sha256(payload_json),
            runtime_reply_type=contract.reply_type,
            safety_status=contract.safety_status,
        )


def test_queue_rejects_unknown_runtime_contract(db_session):
    ticket = _ticket(db_session)
    with pytest.raises(ValueError, match="runtime_contract_version_invalid"):
        queue_outbound_message(
            db_session,
            ticket_id=ticket.id,
            channel=SourceChannel.whatsapp,
            body="Hello",
            created_by=None,
            origin="provider_runtime",
            runtime_trace_id="runtime-trace",
            runtime_contract_version="nexus.ai-reply.v999",
            runtime_signature="signature",
            runtime_reply_type="clarifying_question",
            safety_status="passed",
        )


def test_queue_rejects_null_reply_as_customer_visible(db_session):
    ticket = _ticket(db_session)
    contract = build_ai_reply_contract(
        body=None,
        runtime_trace={"request_id": "rt-null-queue"},
        reply_type="null_reply",
    )
    with pytest.raises(
        ValueError,
        match="ai_reply_null_reply_not_customer_visible",
    ):
        queue_outbound_message(
            db_session,
            ticket_id=ticket.id,
            channel=SourceChannel.whatsapp,
            body="",
            created_by=None,
            origin="provider_runtime",
            runtime_trace_id=contract.runtime_trace_id,
            runtime_contract_version=contract.contract_version,
            runtime_signature=contract.runtime_signature,
            runtime_contract_payload_json=contract.payload_json(
                body=None,
                origin="provider_runtime",
            ),
            runtime_contract_payload_sha256=contract.payload_sha256(
                body=None,
                origin="provider_runtime",
            ),
            runtime_reply_type=contract.reply_type,
            safety_status=contract.safety_status,
        )


def test_queue_rejects_runtime_contract_payload_body_mismatch(db_session):
    ticket = _ticket(db_session)
    contract = build_ai_reply_contract(
        body="Signed body",
        runtime_trace={"request_id": "rt-payload-mismatch"},
        reply_type="clarifying_question",
    )
    with pytest.raises(ValueError, match="runtime_signature_invalid"):
        queue_outbound_message(
            db_session,
            ticket_id=ticket.id,
            channel=SourceChannel.whatsapp,
            body="Different body",
            created_by=None,
            origin="provider_runtime",
            runtime_trace_id=contract.runtime_trace_id,
            runtime_contract_version=contract.contract_version,
            runtime_signature=contract.runtime_signature,
            runtime_contract_payload_json=contract.payload_json(
                body="Signed body",
                origin="provider_runtime",
            ),
            runtime_contract_payload_sha256=contract.payload_sha256(
                body="Signed body",
                origin="provider_runtime",
            ),
            runtime_reply_type=contract.reply_type,
            safety_status=contract.safety_status,
        )


def test_queue_rejects_runtime_contract_payload_origin_mismatch(db_session):
    ticket = _ticket(db_session)
    contract = build_ai_reply_contract(
        body="Signed body",
        runtime_trace={"request_id": "rt-origin-mismatch"},
        reply_type="clarifying_question",
    )
    with pytest.raises(
        ValueError,
        match="runtime_contract_payload_origin_mismatch",
    ):
        queue_outbound_message(
            db_session,
            ticket_id=ticket.id,
            channel=SourceChannel.whatsapp,
            body="Signed body",
            created_by=None,
            origin="provider_runtime",
            runtime_trace_id=contract.runtime_trace_id,
            runtime_contract_version=contract.contract_version,
            runtime_signature=contract.runtime_signature,
            runtime_contract_payload_json=contract.payload_json(
                body="Signed body",
                origin="different_origin",
            ),
            runtime_contract_payload_sha256=contract.payload_sha256(
                body="Signed body",
                origin="different_origin",
            ),
            runtime_reply_type=contract.reply_type,
            safety_status=contract.safety_status,
        )


def test_queue_rejects_runtime_contract_payload_hash_mismatch(db_session):
    ticket = _ticket(db_session)
    contract = build_ai_reply_contract(
        body="Signed body",
        runtime_trace={"request_id": "rt-hash-mismatch"},
        reply_type="clarifying_question",
    )
    with pytest.raises(ValueError, match="runtime_contract_payload_hash_invalid"):
        queue_outbound_message(
            db_session,
            ticket_id=ticket.id,
            channel=SourceChannel.whatsapp,
            body="Signed body",
            created_by=None,
            origin="provider_runtime",
            runtime_trace_id=contract.runtime_trace_id,
            runtime_contract_version=contract.contract_version,
            runtime_signature=contract.runtime_signature,
            runtime_contract_payload_json=contract.payload_json(
                body="Signed body",
                origin="provider_runtime",
            ),
            runtime_contract_payload_sha256="0" * 64,
            runtime_reply_type=contract.reply_type,
            safety_status=contract.safety_status,
        )


def test_queue_accepts_complete_runtime_contract(db_session):
    ticket = _ticket(db_session)
    contract = build_ai_reply_contract(
        body="Complete signed body",
        runtime_trace={"request_id": "rt-complete"},
        reply_type="clarifying_question",
    )
    row = _queue_contract(
        db_session,
        ticket,
        body="Complete signed body",
        contract=contract,
    )
    assert row.runtime_trace_id == contract.runtime_trace_id
    assert row.runtime_contract_version == contract.contract_version
    assert row.runtime_signature == contract.runtime_signature
    assert row.runtime_contract_payload_json
    assert row.runtime_contract_payload_sha256


def test_duplicate_completed_outbound_is_idempotent(db_session, monkeypatch):
    ticket = _ticket(db_session)
    row = TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=SourceChannel.whatsapp,
        status=MessageStatus.sent,
        body="already sent",
        provider_status="sent",
        provider_message_id="provider-message-id",
        sent_at=None,
    )
    db_session.add(row)
    db_session.flush()
    monkeypatch.setattr(
        "app.services.message_dispatch.dispatch_whatsapp_outbound",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("completed outbound must not dispatch")
        ),
    )

    processed = process_outbound_message(db_session, row)

    assert processed.status == MessageStatus.sent
    assert processed.provider_message_id == "provider-message-id"


def test_runtime_signature_uses_v3_canonical_payload(monkeypatch):
    secret = "runtime-contract-signing-secret-for-test"
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
        body_hash = hashlib.sha256(
            "Grounded answer".encode("utf-8")
        ).hexdigest()
        payload = {
            "body_sha256": body_hash,
            "runtime_trace_id": "rt-v3-hmac",
            "contract_version": AI_REPLY_CONTRACT,
            "safety_status": "passed",
            "reply": {"type": "answer", "text_sha256": body_hash},
            "grounding": {
                "used_sources": ["kb.policy.1#v1:0"],
                "unsupported_claims": [],
                "conflicts": [],
            },
            "risk": {"confidence": 0.91},
            "channel": "webchat",
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        expected = hmac.new(
            secret.encode("utf-8"),
            encoded.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    finally:
        get_settings.cache_clear()

    assert contract.runtime_signature == expected


def test_v3_answer_with_used_sources_passes_outbound_gateway(
    db_session,
    monkeypatch,
):
    ticket = _ticket(db_session)
    body = "Switzerland address changes are allowed before dispatch."
    contract = build_ai_reply_contract(
        body=body,
        runtime_trace={"request_id": "rt-v3-pass"},
        contract_version=AI_REPLY_CONTRACT,
        reply_type="answer",
        used_sources=["knowledge:ch-address-policy"],
        unsupported_claims=[],
        confidence=0.94,
        channel="whatsapp",
    )
    row = _queue_contract(
        db_session,
        ticket,
        body=body,
        contract=contract,
    )
    monkeypatch.setattr(
        "app.services.message_dispatch._external_dispatch_block_reason",
        lambda: None,
    )
    monkeypatch.setattr(
        "app.services.message_dispatch._enforce_customer_visible_policy",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        "app.services.message_dispatch._dispatch_whatsapp_message",
        lambda *args, **kwargs: (
            MessageStatus.sent,
            "whatsapp_test_sent",
            None,
            {"adapter": "test", "idempotency_key": "idem"},
        ),
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
    payload = contract.payload_dict(
        body="Unsupported claim",
        origin="provider_runtime",
    )
    payload["grounding"]["unsupported_claims"] = [
        "mutated unsupported claim"
    ]
    payload_json = canonical_contract_payload_json(payload)

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
            runtime_contract_payload_sha256=contract_payload_sha256(payload_json),
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


def test_signed_ai_outbound_body_cannot_be_mutated_after_signature(
    db_session,
    monkeypatch,
):
    ticket = _ticket(db_session)
    contract = build_ai_reply_contract(
        body="Exact signed body",
        runtime_trace={"request_id": "rt-mutation"},
        reply_type="clarifying_question",
    )
    row = _queue_contract(
        db_session,
        ticket,
        body="Exact signed body",
        contract=contract,
    )
    monkeypatch.setattr(
        "app.services.message_dispatch._external_dispatch_block_reason",
        lambda: None,
    )
    monkeypatch.setattr(
        "app.services.message_dispatch.evaluate_customer_visible_policy",
        lambda *args, **kwargs: CustomerVisiblePolicyDecision(
            True,
            "allow",
            [],
            "Exact signed body ",
        ),
    )
    monkeypatch.setattr(
        "app.services.message_dispatch.dispatch_whatsapp_outbound",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("dispatch must not run")
        ),
    )

    processed = process_outbound_message(db_session, row)

    assert processed.status == MessageStatus.dead
    assert processed.failure_code == "runtime_signed_body_mutation"
    assert processed.body == "Exact signed body"


def test_webchat_ai_reply_uses_customer_visible_message_service():
    source = (ROOT / "app/services/webchat_ai_service.py").read_text(
        encoding="utf-8"
    )
    assert "create_customer_visible_message" in source
    assert "TicketOutboundMessage(" not in source
    assert "queue_outbound_message" not in source


def test_ai_reply_does_not_update_last_human_update():
    source = (ROOT / "app/services/webchat_ai_service.py").read_text(
        encoding="utf-8"
    )
    assert "ticket.last_human_update" not in source
    assert "ticket.last_ai_update = final_body" in source


def test_dispatch_has_no_ticket_text_fact_evidence_fallback():
    source = (ROOT / "app/services/message_dispatch.py").read_text(
        encoding="utf-8"
    )
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

    result = record_runtime_null_reply(
        db_session,
        ticket=ticket,
        ai_contract=contract,
    )

    assert result.outbound_message is None
    assert result.customer_visible is False
    assert (
        db_session.query(TicketOutboundMessage)
        .filter(TicketOutboundMessage.ticket_id == ticket.id)
        .count()
        == 0
    )
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
    source = (ROOT / "app/services/webchat_service.py").read_text(
        encoding="utf-8"
    )
    assert "webchat_handoff_ack" not in source
    assert 'origin="handoff_notice"' not in source


def test_webchat_ai_does_not_directly_create_customer_visible_webchat_message():
    source = (ROOT / "app/services/webchat_ai_service.py").read_text(
        encoding="utf-8"
    )
    assert "create_customer_visible_message" in source
    assert "WebchatMessage(" not in source


def test_webchat_ai_does_not_directly_create_external_ticket_comment():
    source = (ROOT / "app/services/webchat_ai_service.py").read_text(
        encoding="utf-8"
    )
    assert "TicketComment(" not in source
    assert "visibility=NoteVisibility.external" not in source


def test_admin_reply_uses_customer_visible_message_service_for_visible_entities():
    source = (ROOT / "app/services/webchat_service.py").read_text(
        encoding="utf-8"
    )
    admin_reply = source.split("def admin_reply(", 1)[1]
    assert "create_customer_visible_message(" in admin_reply
    assert "WebchatMessage(" not in admin_reply
    assert "TicketComment(" not in admin_reply
    assert "TicketEvent(" not in admin_reply


def test_originless_external_outbound_is_blocked_after_contract_cutover(
    db_session,
    monkeypatch,
):
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
    monkeypatch.setattr(
        "app.services.message_dispatch.dispatch_whatsapp_outbound",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("dispatch must not run")
        ),
    )

    processed = process_outbound_message(db_session, row)

    assert processed.status == MessageStatus.dead
    assert processed.failure_code == "missing_customer_visible_origin_contract"
