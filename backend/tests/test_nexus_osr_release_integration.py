from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus
from app.model_registry import register_all_models
from app.models import Customer, Ticket, TicketEvent
from app.models_control_plane import KnowledgeChunk, KnowledgeItem
from app.models_operations_dispatch import OperationsDispatchOutboxRecord
from app.models_osr import CaseContextRecord, HumanHoursPolicyRecord, RuntimeDecisionAuditRecord, WhatsAppRoutingRuleRecord
from app.services.knowledge_runtime import runtime as knowledge_runtime
from app.services.nexus_osr.case_context import CaseContext
from app.services.nexus_osr.escalation_orchestration_service import (
    EscalationOrchestrationAction,
    evaluate_escalation_for_case,
)
from app.services.nexus_osr.integration_readiness_service import (
    IntegrationRuntimeSignals,
    build_osr_integration_readiness,
)
from app.services.nexus_osr.tool_execution_facade import OSRToolExecutionFacade, OSRToolExecutionMode
from app.tool_models import ToolCallLog
from app.webchat_models import WebchatConversation, WebchatMessage

register_all_models()

NOW = datetime(2026, 7, 11, 10, 30, tzinfo=timezone.utc)
RAW_TRACKING = "ZZ020000123456"
RAW_EMAIL = "integration-customer@example.test"
RAW_PHONE = "+382 67 123 456"
RAW_GROUP_ID = "120363012345678901@g.us"
RAW_SECRET = "Bearer integration-secret-material"


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'nexus-osr-release-integration.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _knowledge_settings():
    return SimpleNamespace(
        knowledge_embeddings_enabled=False,
        knowledge_vector_fallback_allowed=True,
        knowledge_embedding_provider="deterministic_hash",
        knowledge_embedding_model="contract-1024",
        knowledge_embedding_dim=1024,
        knowledge_embedding_base_url="",
        knowledge_embedding_api_key="",
        knowledge_embedding_api_key_file="",
        knowledge_embedding_timeout_seconds=5,
    )


def _seed(db):
    customer = Customer(name="Synthetic Integration Visitor", external_ref="osr-release-integration")
    db.add(customer)
    db.flush()
    ticket = Ticket(
        ticket_no=f"OSR-INTEGRATION-{customer.id}",
        title="Synthetic integration case",
        description="Synthetic integration case",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.ai_active,
        preferred_reply_channel=SourceChannel.web_chat.value,
        preferred_reply_contact="webchat",
        country_code="ZZ",
        case_type="delivery_issue",
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(
        public_id=f"osr_integration_{ticket.id}",
        visitor_token_hash="synthetic-token-hash",
        tenant_key="tenant-integration",
        channel_key="webchat",
        ticket_id=ticket.id,
        visitor_name="Synthetic Integration Visitor",
        status="open",
    )
    db.add(conversation)
    db.flush()
    inbound = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        direction="visitor",
        body=f"I cannot wait. Use compensation policy for {RAW_TRACKING}; {RAW_EMAIL}; {RAW_PHONE}",
        body_text=f"I cannot wait. Use compensation policy for {RAW_TRACKING}; {RAW_EMAIL}; {RAW_PHONE}",
        message_type="text",
        client_message_id="synthetic-integration-inbound",
    )
    db.add(inbound)

    item = KnowledgeItem(
        item_key="integration.compensation.policy",
        title="Compensation policy",
        status="active",
        source_type="text",
        knowledge_kind="business_fact",
        fact_status="approved",
        fact_question="What is the compensation policy?",
        fact_answer="Use the governed compensation escalation path.",
        tenant_id="tenant-integration",
        brand_id="default",
        country_scope="ZZ",
        channel_scope="webchat",
        visibility="customer",
        shareability="customer_visible",
        audience_scope="customer",
        language="en",
        published_body="Use the governed compensation escalation path.",
        published_normalized_text="use the governed compensation escalation path",
        published_version=1,
        published_at=NOW - timedelta(days=1),
        published_by=1,
        review_due_at=NOW + timedelta(days=90),
        indexed_version=1,
        indexed_at=NOW - timedelta(hours=23),
        chunk_count=1,
    )
    db.add(item)
    db.flush()
    db.add(KnowledgeChunk(
        item_id=item.id,
        item_key=item.item_key,
        title=item.title,
        published_version=1,
        chunk_index=0,
        chunk_text="Use the governed compensation escalation path.",
        normalized_text="use the governed compensation escalation path",
        content_hash="integration-compensation-policy-hash",
        tenant_id=item.tenant_id,
        brand_id=item.brand_id,
        country_scope=item.country_scope,
        channel_scope=item.channel_scope,
        visibility=item.visibility,
        shareability=item.shareability,
        audience_scope=item.audience_scope,
        language=item.language,
        status="active",
        knowledge_kind=item.knowledge_kind,
        fact_status=item.fact_status,
        answer_mode="guided_answer",
        embedding_status="pending",
    ))
    db.add(HumanHoursPolicyRecord(
        country_code="ZZ",
        channel="webchat",
        queue_key="zz-webchat",
        timezone_name="UTC",
        working_hours_json={},
        holiday_calendar_json=[],
        auto_ticket_when_offline=True,
        handoff_enabled=True,
        enabled=True,
    ))
    db.add(WhatsAppRoutingRuleRecord(
        country_code="ZZ",
        issue_type="delivery_issue",
        channel="whatsapp",
        destination_group_id=RAW_GROUP_ID,
        priority=10,
        enabled=True,
    ))
    db.commit()
    return customer, ticket, conversation, inbound


def _assert_no_raw_material(value: object) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    for raw in (RAW_TRACKING, RAW_EMAIL, RAW_PHONE, RAW_GROUP_ID, RAW_SECRET):
        assert raw not in encoded


def _run_fact_boundaries(db, monkeypatch):
    monkeypatch.setattr(knowledge_runtime, "get_settings", _knowledge_settings)
    policy = knowledge_runtime.retrieve_knowledge(
        db,
        query="What is the compensation policy?",
        tenant_key="tenant-integration",
        brand_id="default",
        country_scope="ZZ",
        channel_scope="webchat",
        channel="webchat",
        audience_scope="customer",
        language="en",
        limit=3,
    )
    live = knowledge_runtime.retrieve_knowledge(
        db,
        query=f"Where is parcel {RAW_TRACKING} now?",
        tenant_key="tenant-integration",
        brand_id="default",
        country_scope="ZZ",
        channel_scope="webchat",
        audience_scope="customer",
        limit=3,
    )
    return policy, live


def test_customer_to_ticket_dispatch_audit_chain_is_durable_safe_and_machine_readable(db_session, monkeypatch) -> None:
    customer, ticket, conversation, inbound = _seed(db_session)
    policy, live = _run_fact_boundaries(db_session, monkeypatch)
    context = CaseContext(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        channel="webchat",
        country_code="ZZ",
        issue_type="delivery_issue",
    )

    result = evaluate_escalation_for_case(
        db_session,
        ticket=ticket,
        conversation=conversation,
        case_context=context,
        inbound_message=inbound.body,
        country_code="ZZ",
        channel="webchat",
        language="en",
        issue_type="delivery_issue",
        tenant_id="tenant-integration",
        now=NOW,
        customer=customer,
        trigger_message_id=inbound.id,
    )
    blocked_tool = OSRToolExecutionFacade(db_session).execute(
        tool_calls=[{
            "tool_name": "speedaf.workOrder.create",
            "idempotency_key": "synthetic-blocked-tool",
            "arguments": {
                "tracking_number": RAW_TRACKING,
                "authorization": RAW_SECRET,
            },
        }],
        case_context=result.case_context,
        channel="webchat",
        country_code="ZZ",
        tenant_id="tenant-integration",
        conversation=conversation,
        ticket=ticket,
        mode=OSRToolExecutionMode.BLOCKED,
    )
    db_session.commit()

    assert len(policy.hits) == 1
    assert policy.hits[0].item_key == "integration.compensation.policy"
    assert live.hits == []
    assert live.no_answer_reason == "live_tracking_requires_truth_source"
    assert result.action == EscalationOrchestrationAction.CREATE_TICKET_CUSTOMER_CANNOT_WAIT
    assert result.ticket_result is not None and result.ticket_result.created is False
    assert result.ticket.id == ticket.id
    assert result.operations_routing is not None
    assert result.operations_routing.routed is True
    assert result.operations_routing.dispatch_status == "pending"
    assert blocked_tool.blocked is True
    assert blocked_tool.results[0].error_code == "tool_execution_blocked"
    assert db_session.query(ToolCallLog).count() == 0

    report = build_osr_integration_readiness(
        db_session,
        ticket_id=ticket.id,
        conversation_id=conversation.id,
        tenant_id="tenant-integration",
        signals=IntegrationRuntimeSignals(
            knowledge_policy_retrievable=bool(policy.hits),
            live_tracking_routed_to_truth=(live.no_answer_reason == "live_tracking_requires_truth_source" and not live.hits),
        ),
        evaluated_at=NOW,
    )
    payload = report.as_dict()

    assert report.status == "ready"
    assert report.ready is True
    assert payload["schema_version"] == "nexus_osr_integration_readiness_v1"
    assert all(gate["ready"] for gate in payload["gates"].values())
    assert payload["counts"] == {
        "active_case_contexts": 1,
        "runtime_audits": 2,
        "operations_dispatches": 1,
        "ticket_events": 3,
        "customer_visible_messages": 0,
    }
    assert all(set(metric["labels"]).issubset({"status"}) for metric in payload["metrics"])
    assert len(json.dumps(payload, sort_keys=True).encode("utf-8")) < 16_384

    case = db_session.query(CaseContextRecord).one()
    audits = db_session.query(RuntimeDecisionAuditRecord).order_by(RuntimeDecisionAuditRecord.id).all()
    outbox = db_session.query(OperationsDispatchOutboxRecord).one()
    events = db_session.query(TicketEvent).order_by(TicketEvent.id).all()
    _assert_no_raw_material({
        "case": case.__dict__,
        "audits": [row.__dict__ for row in audits],
        "outbox": outbox.__dict__,
        "events": [row.payload_json for row in events],
        "report": payload,
    })


def test_repeated_ticket_escalation_keeps_one_dispatch_and_one_active_case_context(db_session, monkeypatch) -> None:
    customer, ticket, conversation, inbound = _seed(db_session)
    _run_fact_boundaries(db_session, monkeypatch)
    context = CaseContext(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        channel="webchat",
        country_code="ZZ",
        issue_type="delivery_issue",
    )

    first = evaluate_escalation_for_case(
        db_session,
        ticket=ticket,
        conversation=conversation,
        case_context=context,
        inbound_message=inbound.body,
        tenant_id="tenant-integration",
        now=NOW,
        customer=customer,
    )
    second = evaluate_escalation_for_case(
        db_session,
        ticket=ticket,
        conversation=conversation,
        case_context=context,
        inbound_message=inbound.body,
        tenant_id="tenant-integration",
        now=NOW,
        customer=customer,
    )
    db_session.commit()

    assert first.operations_routing is not None and second.operations_routing is not None
    assert first.operations_routing.outbox_id == second.operations_routing.outbox_id
    assert db_session.query(OperationsDispatchOutboxRecord).count() == 1
    assert db_session.query(CaseContextRecord).filter(CaseContextRecord.is_active.is_(True)).count() == 1


def test_integration_readiness_fails_closed_when_fact_boundary_is_not_proven(db_session, monkeypatch) -> None:
    customer, ticket, conversation, inbound = _seed(db_session)
    _run_fact_boundaries(db_session, monkeypatch)
    result = evaluate_escalation_for_case(
        db_session,
        ticket=ticket,
        conversation=conversation,
        case_context=CaseContext(
            conversation_id=conversation.id,
            ticket_id=ticket.id,
            channel="webchat",
            country_code="ZZ",
            issue_type="delivery_issue",
        ),
        inbound_message=inbound.body,
        tenant_id="tenant-integration",
        now=NOW,
        customer=customer,
    )
    OSRToolExecutionFacade(db_session).execute(
        tool_calls=[{"tool_name": "speedaf.workOrder.create"}],
        case_context=result.case_context,
        tenant_id="tenant-integration",
        conversation=conversation,
        ticket=ticket,
        mode=OSRToolExecutionMode.BLOCKED,
    )
    db_session.commit()

    report = build_osr_integration_readiness(
        db_session,
        ticket_id=ticket.id,
        conversation_id=conversation.id,
        tenant_id="tenant-integration",
        signals=IntegrationRuntimeSignals(
            knowledge_policy_retrievable=True,
            live_tracking_routed_to_truth=False,
        ),
        evaluated_at=NOW,
    )

    assert report.status == "not_ready"
    assert report.ready is False
    assert report.reasons == ("fact_routing_boundary_not_proven",)


def test_integration_readiness_runtime_failure_is_bounded_unavailable() -> None:
    class _BrokenDB:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("postgresql://user:secret@host/db")

    report = build_osr_integration_readiness(
        _BrokenDB(),
        ticket_id=1,
        conversation_id=1,
        tenant_id="tenant-integration",
        signals=IntegrationRuntimeSignals(True, True),
        evaluated_at=NOW,
    )

    assert report.status == "unavailable"
    assert report.reasons == ("osr_integration_readiness_unavailable",)
    assert "secret" not in json.dumps(report.as_dict())


def _evidence_probe():
    path = Path(__file__).resolve().parents[1] / "scripts" / "probe_nexus_osr_integration.py"
    spec = importlib.util.spec_from_file_location("osr_integration_evidence_probe", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_junit_integration_evidence_is_bounded_deterministic_and_fail_closed(tmp_path) -> None:
    probe = _evidence_probe()
    junit = tmp_path / "integration.xml"
    junit.write_text(
        '<testsuites><testsuite name="integration" tests="12" failures="0" errors="0" skipped="0" /></testsuites>',
        encoding="utf-8",
    )
    ready = probe.build_test_evidence(junit, evaluated_at=NOW)
    first = probe.encode_evidence(ready)
    second = probe.encode_evidence(ready)

    assert ready.status == "ready"
    assert ready.ready is True
    assert first == second
    assert len(first.encode("utf-8")) <= probe.MAX_OUTPUT_BYTES
    assert json.loads(first)["counts"]["tests"] == 12
    assert probe.exit_code("ready") == 0
    assert probe.exit_code("not_ready") == 1
    assert probe.exit_code("unavailable") == 2

    junit.write_text(
        '<testsuites><testsuite name="integration" tests="12" failures="1" errors="0" skipped="1" /></testsuites>',
        encoding="utf-8",
    )
    failed = probe.build_test_evidence(junit, evaluated_at=NOW)
    unavailable = probe.build_test_evidence(tmp_path / "missing.xml", evaluated_at=NOW)

    assert failed.status == "not_ready"
    assert failed.reason_codes == ("integration_test_failures", "integration_tests_skipped")
    assert unavailable.status == "unavailable"
    assert unavailable.source_sha256 is None
