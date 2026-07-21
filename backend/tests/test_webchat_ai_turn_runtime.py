from __future__ import annotations

import json
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.db import Base, SessionLocal, engine
from app.enums import UserRole
from app.main import app
from app.models import BackgroundJob, Ticket, User
from app.models_osr import CaseContextRecord, RuntimeDecisionAuditRecord
from app.models_webchat_debug import WebchatAIDebugRun, WebchatAITestFinding
from app.services.agent_runtime.terminal_reply import customer_visible_fallback
from app.services.background_jobs import WEBCHAT_AI_REPLY_JOB, dispatch_pending_webchat_ai_reply_jobs
from app.services.webchat_debug_bundle_service import build_ai_debug_bundle, create_test_finding
from app.services.webchat_runtime_ai_service import WebchatRuntimeReplyResult
from app.utils.time import utc_now
from app.webchat_models import WebchatAITurn, WebchatConversation, WebchatEvent, WebchatHandoffRequest, WebchatMessage  # noqa: F401


def _ensure_schema_and_user() -> None:
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        columns = {col["name"] for col in inspect(conn).get_columns("webchat_ai_turns")}
        if "runtime_trace_json" not in columns:
            conn.execute(text("ALTER TABLE webchat_ai_turns ADD COLUMN runtime_trace_json TEXT"))
        outbound_columns = {col["name"] for col in inspect(conn).get_columns("ticket_outbound_messages")}
        outbound_contract_columns = {
            "origin": "VARCHAR(40)",
            "runtime_trace_id": "VARCHAR(120)",
            "runtime_contract_version": "VARCHAR(80)",
            "runtime_signature": "VARCHAR(128)",
            "runtime_contract_payload_json": "TEXT",
            "runtime_contract_payload_sha256": "VARCHAR(64)",
            "runtime_reply_type": "VARCHAR(40)",
            "safety_status": "VARCHAR(40)",
        }
        for column_name, column_type in outbound_contract_columns.items():
            if column_name not in outbound_columns:
                conn.execute(text(f"ALTER TABLE ticket_outbound_messages ADD COLUMN {column_name} {column_type}"))
        ticket_columns = {col["name"] for col in inspect(conn).get_columns("tickets")}
        ticket_ai_columns = {
            "last_ai_update": "TEXT",
            "last_runtime_reply_at": "DATETIME",
        }
        for column_name, column_type in ticket_ai_columns.items():
            if column_name not in ticket_columns:
                conn.execute(text(f"ALTER TABLE tickets ADD COLUMN {column_name} {column_type}"))
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.id == 98765).first():
            db.add(User(id=98765, username="webchat_ai_turn_admin", display_name="WebChat AI Turn Admin", password_hash="test", role=UserRole.admin, is_active=True))
            db.commit()
    finally:
        db.close()


def _init_conversation(client: TestClient):
    init = client.post('/api/webchat/init', json={
        'tenant_key': 'turn-runtime-pytest',
        'channel_key': 'website',
        'visitor_name': 'Turn Runtime Visitor',
        'origin': 'https://example.test',
        'page_url': 'https://example.test/help',
    })
    assert init.status_code == 200, init.text
    payload = init.json()
    return payload['conversation_id'], payload['visitor_token']


def _send(client: TestClient, conversation_id: str, visitor_token: str, body: str, client_message_id: str):
    res = client.post(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        json={'body': body, 'client_message_id': client_message_id},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _clear_webchat_ai_jobs() -> None:
    db = SessionLocal()
    try:
        job_ids = [row[0] for row in db.query(BackgroundJob.id).filter(BackgroundJob.job_type == WEBCHAT_AI_REPLY_JOB).all()]
        if job_ids:
            db.query(WebchatAITurn).filter(WebchatAITurn.job_id.in_(job_ids)).update(
                {WebchatAITurn.job_id: None},
                synchronize_session=False,
            )
        db.query(BackgroundJob).filter(BackgroundJob.job_type == WEBCHAT_AI_REPLY_JOB).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()



def test_v3_reply_contract_records_customer_and_tool_sources():
    from app.enums import SourceChannel
    from app.services.webchat_ai_service import _ai_reply_contract_fields

    fields = _ai_reply_contract_fields(
        body="The approved answer.",
        channel=SourceChannel.web_chat,
        handoff_required=False,
        runtime_trace={
            "ai_decision": {"confidence": 0.9},
            "executed_tools": [
                {
                    "tool_name": "knowledge.search",
                    "ok": True,
                    "status": "executed",
                }
            ],
        },
        reply_type="answer",
    )

    assert fields["contract_version"] == "nexus.ai_reply.v3"
    assert fields["reply_type"] == "answer"
    assert fields["used_sources"] == [
        "context:customer_message",
        "tool:knowledge.search",
    ]
    assert fields["unsupported_claims"] == []
    assert fields["confidence"] == 0.9


def test_webchat_ai_turn_is_created_and_public_poll_reports_pending():
    _ensure_schema_and_user()
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)

    sent = _send(client, conversation_id, visitor_token, 'Where is my parcel?', 'turn-runtime-1')
    assert sent['ai_pending'] is True
    assert sent['ai_status'] == 'queued'
    assert sent['ai_turn_id']

    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        assert conversation is not None
        turns = db.query(WebchatAITurn).filter(WebchatAITurn.conversation_id == conversation.id).all()
        assert len(turns) == 1
        assert turns[0].status == 'queued'
        assert conversation.active_ai_turn_id == turns[0].id
        assert db.query(BackgroundJob).filter(BackgroundJob.job_type == WEBCHAT_AI_REPLY_JOB, BackgroundJob.dedupe_key == f'webchat-ai-turn:{turns[0].id}').count() == 1
        assert db.query(BackgroundJob).filter(BackgroundJob.job_type == WEBCHAT_AI_REPLY_JOB, BackgroundJob.dedupe_key == f"webchat-ai-reply:{sent['message']['id']}").count() == 0
        assert db.query(WebchatEvent).filter(WebchatEvent.conversation_id == conversation.id, WebchatEvent.event_type == 'ai_turn.queued').count() >= 1
    finally:
        db.close()

    polled = client.get(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
    )
    assert polled.status_code == 200, polled.text
    assert polled.json()['ai_pending'] is True


def test_duplicate_client_message_id_is_idempotent():
    _ensure_schema_and_user()
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)

    first = _send(client, conversation_id, visitor_token, 'Hello once', 'turn-runtime-idem-1')
    second = _send(client, conversation_id, visitor_token, 'Hello once', 'turn-runtime-idem-1')

    assert second['idempotent'] is True
    assert second['message']['id'] == first['message']['id']

    db = SessionLocal()
    try:
        cid = db.query(WebchatConversation.id).filter(WebchatConversation.public_id == conversation_id).scalar()
        assert db.query(WebchatMessage).filter(WebchatMessage.conversation_id == cid, WebchatMessage.direction == 'visitor', WebchatMessage.client_message_id == 'turn-runtime-idem-1').count() == 1
    finally:
        db.close()


def test_queued_turn_coalesces_consecutive_visitor_messages():
    _ensure_schema_and_user()
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)

    first = _send(client, conversation_id, visitor_token, 'Where is my parcel?', 'turn-runtime-coalesce-1')
    second = _send(client, conversation_id, visitor_token, 'Tracking number is ABC1234567', 'turn-runtime-coalesce-2')
    assert first['ai_turn_id'] == second['ai_turn_id']
    assert second['coalesced'] is True

    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        assert conversation is not None
        turns = db.query(WebchatAITurn).filter(WebchatAITurn.conversation_id == conversation.id).all()
        assert len(turns) == 1
        second_message = (
            db.query(WebchatMessage)
            .filter(
                WebchatMessage.conversation_id == conversation.id,
                WebchatMessage.client_message_id == 'turn-runtime-coalesce-2',
            )
            .order_by(WebchatMessage.id.desc())
            .first()
        )
        assert second_message is not None
        assert turns[0].latest_visitor_message_id == second_message.id
        assert db.query(BackgroundJob).filter(BackgroundJob.job_type == WEBCHAT_AI_REPLY_JOB, BackgroundJob.dedupe_key == f'webchat-ai-turn:{turns[0].id}').count() == 1
    finally:
        db.close()


def test_processing_turn_queues_next_turn_for_new_message():
    _ensure_schema_and_user()
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)

    first = _send(client, conversation_id, visitor_token, 'First question', 'turn-runtime-next-1')
    first_turn_id = first['ai_turn_id']

    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        turn = db.query(WebchatAITurn).filter(WebchatAITurn.id == first_turn_id).first()
        assert conversation is not None and turn is not None
        turn.status = 'bridge_calling'
        turn.context_cutoff_message_id = turn.latest_visitor_message_id or turn.trigger_message_id
        conversation.active_ai_status = 'bridge_calling'
        conversation.active_ai_context_cutoff_message_id = turn.context_cutoff_message_id
        db.commit()
    finally:
        db.close()

    second = _send(client, conversation_id, visitor_token, 'Second question', 'turn-runtime-next-2')
    assert second['ai_turn_id'] == first_turn_id
    assert second['next_ai_turn_id']

    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        assert conversation is not None
        assert conversation.next_ai_turn_id == second['next_ai_turn_id']
        turns = db.query(WebchatAITurn).filter(WebchatAITurn.conversation_id == conversation.id).order_by(WebchatAITurn.id.asc()).all()
        assert len(turns) == 2
        assert turns[1].status == 'queued'
    finally:
        db.close()


def test_stale_turn_is_superseded_and_does_not_write_agent_reply(monkeypatch):
    _ensure_schema_and_user()
    from app.services import webchat_ai_orchestration_service
    monkeypatch.setattr(webchat_ai_orchestration_service.settings, 'webchat_ai_auto_reply_mode', 'off')
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)

    first = _send(client, conversation_id, visitor_token, 'Old question', 'turn-runtime-stale-1')
    first_turn_id = first['ai_turn_id']

    db = SessionLocal()
    try:
        first_message_id = first['message']['id']
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        turn = db.query(WebchatAITurn).filter(WebchatAITurn.id == first_turn_id).first()
        first_message = db.query(WebchatMessage).filter(WebchatMessage.id == first_message_id).first()
        assert conversation is not None and turn is not None and first_message is not None
        turn.status = 'bridge_calling'
        turn.context_cutoff_message_id = first_message.id
        conversation.active_ai_status = 'bridge_calling'
        conversation.active_ai_context_cutoff_message_id = first_message.id
        db.commit()
    finally:
        db.close()

    _send(client, conversation_id, visitor_token, 'Newer question', 'turn-runtime-stale-2')

    monkeypatch.setattr(webchat_ai_orchestration_service.settings, 'webchat_ai_auto_reply_mode', 'runtime')

    db = SessionLocal()
    try:
        first_message_id = first['message']['id']
        first_message = db.query(WebchatMessage).filter(WebchatMessage.id == first_message_id).first()
        result = webchat_ai_orchestration_service.process_webchat_ai_reply_job(db, conversation_id=first_message.conversation_id, ticket_id=first_message.ticket_id, visitor_message_id=first_message.id)
        db.commit()
        assert result['status'] == 'superseded'
        turn = db.query(WebchatAITurn).filter(WebchatAITurn.id == first_turn_id).first()
        assert turn is not None
        assert turn.status == 'superseded'
        assert db.query(WebchatMessage).filter(WebchatMessage.conversation_id == first_message.conversation_id, WebchatMessage.direction == 'agent', WebchatMessage.ai_turn_id == first_turn_id).count() == 0
    finally:
        db.close()


def _patch_ticketless_dependencies(monkeypatch, conversation_ai_service) -> None:
    del monkeypatch, conversation_ai_service

def _runtime_reply(
    *,
    reply: str,
    intent: str = "general_support",
    handoff_required: bool = False,
    handoff_reason: str | None = None,
    recommended_agent_action: str | None = None,
    elapsed_ms: int = 21,
    runtime_trace: dict | None = None,
) -> WebchatRuntimeReplyResult:
    return WebchatRuntimeReplyResult(
        ok=True,
        ai_generated=True,
        reply_source="private_ai_runtime",
        reply=reply,
        intent=intent,
        handoff_required=handoff_required,
        handoff_reason=handoff_reason,
        recommended_agent_action=recommended_agent_action,
        elapsed_ms=elapsed_ms,
        runtime_trace=runtime_trace,
        tool_calls=[],
    )


def test_ai_turn_completes_and_clears_pending_after_dispatch(monkeypatch):
    _ensure_schema_and_user()
    _clear_webchat_ai_jobs()
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)

    sent = _send(client, conversation_id, visitor_token, 'Hello', 'turn-runtime-dispatch-1')
    ai_turn_id = sent['ai_turn_id']

    from app.services import conversation_ai_service, webchat_ai_orchestration_service
    _patch_ticketless_dependencies(monkeypatch, conversation_ai_service)
    monkeypatch.setattr(webchat_ai_orchestration_service.settings, 'webchat_ai_auto_reply_mode', 'runtime')

    def fake_run_runtime(**_kwargs):
        return _runtime_reply(
            reply='Hi, how can I help you today?',
            runtime_trace={
                "latency_class": "agent_runtime",
                "prompt_profile": "generic_agent",
                "prompt_chars": 1709,
                "elapsed_ms": 10017,
                "model": "qwen2.5:3b",
                "chat_mode": "direct",
                "request_shape": "question",
                "endpoint": "https://apis.speedaf.com/open-api/mcp/order/query?appCode=SHOULD_NOT_PERSIST",
                "authorization": "Bearer SHOULD_NOT_PERSIST",
                "prompt": "customer text SHOULD_NOT_PERSIST",
            },
        )

    monkeypatch.setattr(conversation_ai_service, '_run_runtime', fake_run_runtime)

    db = SessionLocal()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.dedupe_key == f'webchat-ai-turn:{ai_turn_id}').first()
        assert job is not None
        job.next_run_at = None
        db.commit()
        processed = dispatch_pending_webchat_ai_reply_jobs(db, worker_id='turn-runtime-worker')
        assert any(item.job_type == WEBCHAT_AI_REPLY_JOB for item in processed)
        db.commit()
    finally:
        db.close()

    polled = client.get(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
    )
    assert polled.status_code == 200, polled.text
    payload = polled.json()
    assert payload['ai_pending'] is False
    agent_messages = [msg for msg in payload['messages'] if msg['author_label'] == 'AI Assistant']
    assert agent_messages
    assert agent_messages[0].get('ai_turn_id') == ai_turn_id

    db = SessionLocal()
    try:
        turn = db.query(WebchatAITurn).filter(WebchatAITurn.id == ai_turn_id).first()
        assert turn is not None
        assert turn.ticket_id is None
        assert turn.status == 'completed'
        assert turn.reply_message_id is not None
        trace = json.loads(turn.runtime_trace_json or '{}')
        assert trace["latency_class"] == "agent_runtime"
        assert trace["prompt_profile"] == "generic_agent"
        assert trace["prompt_chars"] == 1709
        assert trace["model"] == "qwen2.5:3b"
        trace_text = json.dumps(trace, ensure_ascii=False)
        assert "SHOULD_NOT_PERSIST" not in trace_text
        assert "CH020000129135" not in trace_text
        message = db.query(WebchatMessage).filter(
            WebchatMessage.ai_turn_id == ai_turn_id,
            WebchatMessage.direction == 'agent',
        ).one()
        assert message.ticket_id is None
        metadata = json.loads(message.metadata_json or '{}')
        assert metadata['ticketless_conversation'] is True
        metadata_text = json.dumps(metadata, ensure_ascii=False)
        assert "SHOULD_NOT_PERSIST" not in metadata_text
        assert "CH020000129135" not in metadata_text

        audit = db.query(RuntimeDecisionAuditRecord).filter(
            RuntimeDecisionAuditRecord.conversation_id == turn.conversation_id,
            RuntimeDecisionAuditRecord.ticket_id.is_(None),
        ).order_by(RuntimeDecisionAuditRecord.id.desc()).first()
        assert audit is not None
        context = db.query(CaseContextRecord).filter(
            CaseContextRecord.conversation_id == turn.conversation_id,
            CaseContextRecord.ticket_id.is_(None),
        ).order_by(CaseContextRecord.id.desc()).first()
        assert context is not None

        bundle, debug_run = build_ai_debug_bundle(db, turn=turn)
        assert bundle['ticket_id'] is None
        assert debug_run.ticket_id is None
        finding = create_test_finding(
            db,
            run=debug_run,
            current_user_id=None,
            finding_type='other',
            tester_note='Ticketless AI audit remains inspectable.',
        )
        assert finding.ticket_id is None
        assert db.query(WebchatAIDebugRun).filter(
            WebchatAIDebugRun.ai_turn_id == turn.id,
        ).count() == 1
        assert db.query(WebchatAITestFinding).filter(
            WebchatAITestFinding.debug_run_id == debug_run.id,
        ).count() == 1
    finally:
        db.close()


def test_clarifying_question_runtime_reply_is_not_suppressed(monkeypatch):
    _ensure_schema_and_user()
    _clear_webchat_ai_jobs()
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)

    sent = _send(
        client,
        conversation_id,
        visitor_token,
        'I need help, but I have not shared the required reference yet.',
        'turn-runtime-clarify-1',
    )
    ai_turn_id = sent['ai_turn_id']

    from app.services import conversation_ai_service, webchat_ai_orchestration_service
    _patch_ticketless_dependencies(monkeypatch, conversation_ai_service)
    monkeypatch.setattr(webchat_ai_orchestration_service.settings, 'webchat_ai_auto_reply_mode', 'runtime')
    monkeypatch.setattr(
        conversation_ai_service,
        '_run_runtime',
        lambda **_kwargs: _runtime_reply(
            reply='Please share the required reference when you are ready, and I will continue.',
            intent='missing_information',
            elapsed_ms=19,
            runtime_trace={
                'ai_decision_policy_ok': True,
                'ai_decision_intent': 'missing_information',
                'ai_decision_next_action': 'ask_clarifying_question',
            },
        ),
    )

    db = SessionLocal()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.dedupe_key == f'webchat-ai-turn:{ai_turn_id}').first()
        assert job is not None
        job.next_run_at = None
        db.commit()
        processed = dispatch_pending_webchat_ai_reply_jobs(db, worker_id='turn-runtime-clarify-worker')
        assert any(item.job_type == WEBCHAT_AI_REPLY_JOB for item in processed)
        db.commit()
    finally:
        db.close()

    polled = client.get(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
    )
    assert polled.status_code == 200, polled.text
    payload = polled.json()
    agent_messages = [msg for msg in payload['messages'] if msg['author_label'] == 'AI Assistant']
    assert agent_messages
    assert agent_messages[-1]['body'] == 'Please share the required reference when you are ready, and I will continue.'
    assert payload['ai_pending'] is False

    db = SessionLocal()
    try:
        turn = db.query(WebchatAITurn).filter(WebchatAITurn.id == ai_turn_id).first()
        assert turn is not None
        assert turn.ticket_id is None
        assert turn.status == 'completed'
        assert turn.status_reason is None
    finally:
        db.close()


def test_ai_turn_runtime_rejects_handoff_claim_without_tool_side_effect(monkeypatch):
    _ensure_schema_and_user()
    _clear_webchat_ai_jobs()
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)

    sent = _send(
        client,
        conversation_id,
        visitor_token,
        "I need a human to review this",
        "turn-runtime-handoff-without-tool-1",
    )
    ai_turn_id = sent["ai_turn_id"]

    from app.services import conversation_ai_service, webchat_ai_orchestration_service

    monkeypatch.setattr(
        webchat_ai_orchestration_service.settings,
        "webchat_ai_auto_reply_mode",
        "runtime",
    )
    monkeypatch.setattr(
        conversation_ai_service,
        "_run_runtime",
        lambda **_kwargs: _runtime_reply(
            reply="I will connect this conversation to a support agent.",
            intent="handoff_request",
            handoff_required=True,
            handoff_reason="customer_requested_human",
            recommended_agent_action="Human agent should review the customer request.",
            elapsed_ms=34,
            runtime_trace={
                "agent_runtime": True,
                "next_action": "reply",
                "executed_tools": [],
            },
        ),
    )

    db = SessionLocal()
    try:
        ticket_count_before = db.query(Ticket).count()
        job = (
            db.query(BackgroundJob)
            .filter(
                BackgroundJob.dedupe_key
                == f"webchat-ai-turn:{ai_turn_id}"
            )
            .one()
        )
        job.next_run_at = None
        db.commit()
        processed = dispatch_pending_webchat_ai_reply_jobs(
            db,
            worker_id="turn-runtime-handoff-without-tool-worker",
        )
        assert any(item.job_type == WEBCHAT_AI_REPLY_JOB for item in processed)
        db.commit()

        turn = db.get(WebchatAITurn, ai_turn_id)
        assert turn is not None
        assert turn.ticket_id is None
        assert turn.status == "completed"
        assert turn.status_reason is None
        message = (
            db.query(WebchatMessage)
            .filter(
                WebchatMessage.ai_turn_id == ai_turn_id,
                WebchatMessage.direction == "agent",
            )
            .one()
        )
        assert message.body == customer_visible_fallback(
            "en",
            "I need a human to review this",
        )
        metadata = json.loads(message.metadata_json or "{}")
        assert metadata["fallback"] is True
        assert metadata["fallback_reason"] == "handoff_tool_side_effect_missing"
        assert metadata["runtime_handoff_required"] is False
        conversation = db.get(WebchatConversation, turn.conversation_id)
        assert conversation is not None
        assert conversation.ticket_id is None
        assert conversation.current_handoff_request_id is None
        assert (
            db.query(WebchatHandoffRequest)
            .filter(
                WebchatHandoffRequest.conversation_id == conversation.id
            )
            .count()
            == 0
        )
        assert db.query(Ticket).count() == ticket_count_before
    finally:
        db.close()


def test_ai_turn_runtime_human_takeover_during_generation_suppresses_ai_reply(monkeypatch):
    _ensure_schema_and_user()
    _clear_webchat_ai_jobs()
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)

    sent = _send(client, conversation_id, visitor_token, 'I need a human to review this', 'turn-runtime-human-takeover-race')
    ai_turn_id = sent['ai_turn_id']

    from app.services import conversation_ai_service, webchat_ai_orchestration_service
    _patch_ticketless_dependencies(monkeypatch, conversation_ai_service)
    monkeypatch.setattr(webchat_ai_orchestration_service.settings, 'webchat_ai_auto_reply_mode', 'runtime')

    db = SessionLocal()

    def fake_run_runtime(**_kwargs):
        conversation = db.query(WebchatConversation).filter(
            WebchatConversation.public_id == conversation_id,
        ).one()
        conversation.handoff_status = 'accepted'
        conversation.active_agent_id = 98765
        conversation.ai_suspended = True
        conversation.ai_suspended_by = 98765
        conversation.ai_suspended_reason = 'handoff_accepted'
        db.flush()
        return _runtime_reply(
            reply='This AI reply must not be written after human takeover.',
            elapsed_ms=34,
        )

    monkeypatch.setattr(conversation_ai_service, '_run_runtime', fake_run_runtime)

    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.dedupe_key == f'webchat-ai-turn:{ai_turn_id}').first()
        assert job is not None
        job.next_run_at = None
        db.commit()

        processed = dispatch_pending_webchat_ai_reply_jobs(db, worker_id='turn-runtime-human-takeover-race-worker')
        assert any(item.job_type == WEBCHAT_AI_REPLY_JOB for item in processed)
        db.commit()

        turn = db.query(WebchatAITurn).filter(WebchatAITurn.id == ai_turn_id).first()
        assert turn is not None
        assert turn.status == 'superseded'
        assert turn.is_public_reply_allowed is False
        assert db.query(WebchatMessage).filter(WebchatMessage.ai_turn_id == ai_turn_id, WebchatMessage.direction == 'agent').count() == 0
        assert db.query(WebchatEvent).filter(WebchatEvent.conversation_id == turn.conversation_id, WebchatEvent.event_type == 'webchat_ai_reply_suppressed_stale').count() >= 1
    finally:
        db.close()


def test_reconciler_times_out_stale_bridge_calling_turn():
    _ensure_schema_and_user()
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)

    sent = _send(client, conversation_id, visitor_token, 'Timeout please', 'turn-runtime-timeout-1')
    ai_turn_id = sent['ai_turn_id']

    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        turn = db.query(WebchatAITurn).filter(WebchatAITurn.id == ai_turn_id).first()
        assert conversation is not None and turn is not None
        old = utc_now() - timedelta(seconds=3600)
        turn.status = 'bridge_calling'
        turn.updated_at = old
        turn.started_at = old
        conversation.active_ai_status = 'bridge_calling'
        conversation.active_ai_turn_id = turn.id
        conversation.active_ai_updated_at = old
        db.commit()
    finally:
        db.close()

    polled = client.get(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
    )
    assert polled.status_code == 200, polled.text
    payload = polled.json()
    assert payload['ai_pending'] is False

    db = SessionLocal()
    try:
        turn = db.query(WebchatAITurn).filter(WebchatAITurn.id == ai_turn_id).first()
        assert turn is not None
        assert turn.status == 'timeout'
        assert db.query(WebchatEvent).filter(WebchatEvent.conversation_id == turn.conversation_id, WebchatEvent.event_type == 'ai_turn.timeout').count() >= 1
    finally:
        db.close()


def test_failed_ai_turn_persists_safe_runtime_trace():
    from app.services.webchat_ai_turn_service import complete_ai_turn_with_reply

    _ensure_schema_and_user()
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)

    sent = _send(client, conversation_id, visitor_token, 'Where is my parcel CH020000129135?', 'turn-runtime-failed-trace-1')
    ai_turn_id = sent['ai_turn_id']

    db = SessionLocal()
    try:
        turn = db.query(WebchatAITurn).filter(WebchatAITurn.id == ai_turn_id).first()
        assert turn is not None
        conversation = db.query(WebchatConversation).filter(WebchatConversation.id == turn.conversation_id).first()
        assert conversation is not None
        complete_ai_turn_with_reply(
            db,
            conversation=conversation,
            turn=turn,
            result={
                "status": "failed_no_public_reply",
                "reason": "ai_decision_policy_blocked",
                "reply_source": "private_ai_runtime",
                "bridge_elapsed_ms": 9309,
                "runtime_trace": {
                    "model": "qwen2.5:3b",
                    "ai_decision_policy_ok": False,
                    "ai_decision_policy_violation_codes": "raw_tracking_exposed",
                    "ai_decision_checked_tools": "speedaf.order.query",
                    "ai_decision_intent": "tracking",
                        "authorization": "Bearer SHOULD_NOT_PERSIST",
                },
            },
        )
        db.commit()

        db.refresh(turn)
        assert turn.status == "failed"
        assert turn.status_reason == "ai_decision_policy_blocked"
        assert turn.reply_source == "private_ai_runtime"
        assert turn.bridge_elapsed_ms == 9309
        trace = json.loads(turn.runtime_trace_json or "{}")
        assert trace["model"] == "qwen2.5:3b"
        assert trace["ai_decision_policy_ok"] is False
        assert trace["ai_decision_policy_violation_codes"] == "raw_tracking_exposed"
        trace_text = json.dumps(trace, ensure_ascii=False)
        assert "CH020000129135" not in trace_text
        assert "SHOULD_NOT_PERSIST" not in trace_text
    finally:
        db.close()
