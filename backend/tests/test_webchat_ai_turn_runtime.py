from __future__ import annotations

import json
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.orm import object_session

from app.db import Base, SessionLocal, engine
from app.enums import ConversationState, UserRole
from app.main import app
from app.models import BackgroundJob, Ticket, User
from app.services.background_jobs import WEBCHAT_AI_REPLY_JOB, dispatch_pending_webchat_ai_reply_jobs
from app.utils.time import utc_now
from app.webchat_models import WebchatAITurn, WebchatConversation, WebchatEvent, WebchatHandoffRequest, WebchatMessage  # noqa: F401


def _ensure_schema_and_user() -> None:
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        columns = {col["name"] for col in inspect(conn).get_columns("webchat_ai_turns")}
        if "runtime_trace_json" not in columns:
            conn.execute(text("ALTER TABLE webchat_ai_turns ADD COLUMN runtime_trace_json TEXT"))
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
    from app.services import webchat_ai_safe_service
    monkeypatch.setattr(webchat_ai_safe_service.settings, 'webchat_ai_auto_reply_mode', 'off')
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

    monkeypatch.setattr(webchat_ai_safe_service.settings, 'webchat_ai_auto_reply_mode', 'safe_ai')

    db = SessionLocal()
    try:
        first_message_id = first['message']['id']
        first_message = db.query(WebchatMessage).filter(WebchatMessage.id == first_message_id).first()
        result = webchat_ai_safe_service.process_webchat_ai_reply_job(db, conversation_id=first_message.conversation_id, ticket_id=first_message.ticket_id, visitor_message_id=first_message.id)
        db.commit()
        assert result['status'] == 'superseded'
        turn = db.query(WebchatAITurn).filter(WebchatAITurn.id == first_turn_id).first()
        assert turn is not None
        assert turn.status == 'superseded'
        assert db.query(WebchatMessage).filter(WebchatMessage.conversation_id == first_message.conversation_id, WebchatMessage.direction == 'agent', WebchatMessage.ai_turn_id == first_turn_id).count() == 0
    finally:
        db.close()


def test_ai_turn_completes_and_clears_pending_after_dispatch(monkeypatch):
    _ensure_schema_and_user()
    _clear_webchat_ai_jobs()
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)

    sent = _send(client, conversation_id, visitor_token, 'Hello', 'turn-runtime-dispatch-1')
    ai_turn_id = sent['ai_turn_id']

    from app.services import webchat_ai_safe_service, webchat_ai_service
    monkeypatch.setattr(webchat_ai_safe_service.settings, 'webchat_ai_auto_reply_mode', 'safe_ai')

    def fake_generate_ai_reply(**_kwargs):
        webchat_ai_service._LAST_AI_REPLY_SOURCE = 'private_ai_runtime'
        webchat_ai_service._LAST_AI_FALLBACK_REASON = None
        webchat_ai_service._LAST_BRIDGE_ELAPSED_MS = 21
        webchat_ai_service._LAST_BRIDGE_EFFECTIVE_TIMEOUT_SECONDS = 12
        webchat_ai_service._LAST_BRIDGE_WAIT_TIMEOUT_MS = 12000
        webchat_ai_service._LAST_RUNTIME_TRACE = {
            "latency_class": "trusted_tracking_fact",
            "prompt_profile": "trusted_tracking_fact_v1",
            "prompt_chars": 1709,
            "elapsed_ms": 10017,
            "model": "qwen2.5:3b",
            "chat_mode": "direct",
            "request_shape": "question",
            "endpoint": "https://apis.speedaf.com/open-api/mcp/order/query?appCode=SHOULD_NOT_PERSIST",
            "authorization": "Bearer SHOULD_NOT_PERSIST",
            "prompt": "customer text SHOULD_NOT_PERSIST",
            "tracking_number": "CH020000129135",
        }
        return 'Hi, how can I help you today?'

    monkeypatch.setattr(webchat_ai_service, '_generate_ai_reply', fake_generate_ai_reply)

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
        assert turn.status == 'completed'
        assert turn.reply_message_id is not None
        trace = json.loads(turn.runtime_trace_json or '{}')
        assert trace["latency_class"] == "trusted_tracking_fact"
        assert trace["prompt_profile"] == "trusted_tracking_fact_v1"
        assert trace["prompt_chars"] == 1709
        assert trace["model"] == "qwen2.5:3b"
        trace_text = json.dumps(trace, ensure_ascii=False)
        assert "SHOULD_NOT_PERSIST" not in trace_text
        assert "CH020000129135" not in trace_text
        assert db.query(WebchatMessage).filter(WebchatMessage.ai_turn_id == ai_turn_id, WebchatMessage.direction == 'agent').count() == 1
    finally:
        db.close()


def test_tracking_missing_number_runtime_reply_is_not_suppressed(monkeypatch):
    _ensure_schema_and_user()
    _clear_webchat_ai_jobs()
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)

    sent = _send(
        client,
        conversation_id,
        visitor_token,
        'Please help me track my parcel. I will provide the tracking number.',
        'turn-runtime-track-missing-1',
    )
    ai_turn_id = sent['ai_turn_id']

    from app.services import webchat_ai_safe_service, webchat_ai_service
    monkeypatch.setattr(webchat_ai_safe_service.settings, 'webchat_ai_auto_reply_mode', 'safe_ai')

    def fake_generate_ai_reply(**_kwargs):
        webchat_ai_service._LAST_AI_REPLY_SOURCE = 'private_ai_runtime'
        webchat_ai_service._LAST_AI_FALLBACK_REASON = None
        webchat_ai_service._LAST_BRIDGE_ELAPSED_MS = 19
        webchat_ai_service._LAST_BRIDGE_EFFECTIVE_TIMEOUT_SECONDS = 12
        webchat_ai_service._LAST_BRIDGE_WAIT_TIMEOUT_MS = 12000
        webchat_ai_service._LAST_RUNTIME_HANDOFF_REQUIRED = False
        webchat_ai_service._LAST_RUNTIME_HANDOFF_REASON = None
        webchat_ai_service._LAST_RUNTIME_RECOMMENDED_AGENT_ACTION = None
        return 'Share the parcel number when you are ready and I will check the latest tracking details.'

    monkeypatch.setattr(webchat_ai_service, '_generate_ai_reply', fake_generate_ai_reply)

    db = SessionLocal()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.dedupe_key == f'webchat-ai-turn:{ai_turn_id}').first()
        assert job is not None
        job.next_run_at = None
        db.commit()
        processed = dispatch_pending_webchat_ai_reply_jobs(db, worker_id='turn-runtime-track-missing-worker')
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
    assert agent_messages[-1]['body'] == 'Share the parcel number when you are ready and I will check the latest tracking details.'
    assert payload['ai_pending'] is False

    db = SessionLocal()
    try:
        turn = db.query(WebchatAITurn).filter(WebchatAITurn.id == ai_turn_id).first()
        assert turn is not None
        assert turn.status == 'completed'
        assert turn.status_reason is None
    finally:
        db.close()


def test_ai_turn_runtime_handoff_still_writes_ai_reply(monkeypatch):
    _ensure_schema_and_user()
    _clear_webchat_ai_jobs()
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)

    sent = _send(client, conversation_id, visitor_token, 'I need a human to review this', 'turn-runtime-handoff-1')
    ai_turn_id = sent['ai_turn_id']

    from app.services import webchat_ai_safe_service, webchat_ai_service
    monkeypatch.setattr(webchat_ai_safe_service.settings, 'webchat_ai_auto_reply_mode', 'safe_ai')

    def fake_generate_ai_reply(**_kwargs):
        webchat_ai_service._LAST_AI_REPLY_SOURCE = 'private_ai_runtime'
        webchat_ai_service._LAST_AI_FALLBACK_REASON = None
        webchat_ai_service._LAST_BRIDGE_ELAPSED_MS = 34
        webchat_ai_service._LAST_BRIDGE_EFFECTIVE_TIMEOUT_SECONDS = 12
        webchat_ai_service._LAST_BRIDGE_WAIT_TIMEOUT_MS = 12000
        webchat_ai_service._LAST_RUNTIME_HANDOFF_REQUIRED = True
        webchat_ai_service._LAST_RUNTIME_HANDOFF_REASON = 'customer_requested_human'
        webchat_ai_service._LAST_RUNTIME_RECOMMENDED_AGENT_ACTION = 'Human agent should review the customer request.'
        return 'I will connect this conversation to a support agent.'

    monkeypatch.setattr(webchat_ai_service, '_generate_ai_reply', fake_generate_ai_reply)

    db = SessionLocal()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.dedupe_key == f'webchat-ai-turn:{ai_turn_id}').first()
        assert job is not None
        job.next_run_at = None
        db.commit()
        processed = dispatch_pending_webchat_ai_reply_jobs(db, worker_id='turn-runtime-handoff-worker')
        assert any(item.job_type == WEBCHAT_AI_REPLY_JOB for item in processed)
        db.commit()

        turn = db.query(WebchatAITurn).filter(WebchatAITurn.id == ai_turn_id).first()
        assert turn is not None
        assert turn.status == 'completed'
        assert db.query(WebchatMessage).filter(WebchatMessage.ai_turn_id == ai_turn_id, WebchatMessage.direction == 'agent').count() == 1
        conversation = db.query(WebchatConversation).filter(WebchatConversation.id == turn.conversation_id).first()
        assert conversation is not None
        assert conversation.handoff_status == 'requested'
        ticket = db.query(Ticket).filter(Ticket.id == turn.ticket_id).first()
        assert ticket is not None
        assert ticket.conversation_state == ConversationState.human_review_required
        assert ticket.required_action == 'Human agent should review the customer request.'
        assert db.query(WebchatHandoffRequest).filter(WebchatHandoffRequest.conversation_id == conversation.id).count() == 1
    finally:
        db.close()


def test_ai_turn_runtime_human_takeover_during_generation_suppresses_ai_reply(monkeypatch):
    _ensure_schema_and_user()
    _clear_webchat_ai_jobs()
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)

    sent = _send(client, conversation_id, visitor_token, 'I need a human to review this', 'turn-runtime-human-takeover-race')
    ai_turn_id = sent['ai_turn_id']

    from app.services import webchat_ai_safe_service, webchat_ai_service
    monkeypatch.setattr(webchat_ai_safe_service.settings, 'webchat_ai_auto_reply_mode', 'safe_ai')

    def fake_generate_ai_reply(**kwargs):
        conversation = kwargs['conversation']
        conversation.handoff_status = 'accepted'
        conversation.active_agent_id = 98765
        conversation.ai_suspended = True
        conversation.ai_suspended_by = 98765
        conversation.ai_suspended_reason = 'handoff_accepted'
        object_session(conversation).flush()
        webchat_ai_service._LAST_AI_REPLY_SOURCE = 'private_ai_runtime'
        webchat_ai_service._LAST_AI_FALLBACK_REASON = None
        webchat_ai_service._LAST_BRIDGE_ELAPSED_MS = 34
        webchat_ai_service._LAST_RUNTIME_HANDOFF_REQUIRED = False
        return 'This AI reply must not be written after human takeover.'

    monkeypatch.setattr(webchat_ai_service, '_generate_ai_reply', fake_generate_ai_reply)

    db = SessionLocal()
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


def test_runtime_result_blocks_locked_fact_grounding_conflict_before_write():
    from app.services.ai_runtime.schemas import RuntimeAIProviderResult
    from app.services.webchat_runtime_ai_service import _result_from_provider

    provider_result = RuntimeAIProviderResult(
        ok=True,
        ai_generated=True,
        reply_source='private_ai_runtime',
        raw_provider='private_ai_runtime',
        raw_payload_safe_summary={
            'output_contract_repair_applied': True,
            'output_contract_repair_reason': 'locked_fact_grounding_conflict',
            'model': 'qwen2.5:3b',
            'chat_mode': 'direct',
        },
        reply='Sure, we provide domestic to domestic delivery services within Switzerland.',
        intent='other',
        tracking_number=None,
        handoff_required=False,
        handoff_reason=None,
        recommended_agent_action=None,
        elapsed_ms=9000,
    )

    result = _result_from_provider(
        provider_result,
        runtime_context={
            'knowledge_context': {
                'locked_facts': [
                    {
                        'item_key': 'nexus.support.customer.kb.ch.service.availability',
                        'answer': 'Switzerland domestic-to-domestic service is currently unavailable. 瑞士目前暂未开通本对本业务。',
                        'source': {'item_key': 'nexus.support.customer.kb.ch.service.availability'},
                    }
                ]
            }
        },
        body='Do you provide domestic to domestic delivery in Switzerland?',
    )

    assert result.ok is False
    assert result.reply is None
    assert result.error_code == 'locked_fact_grounding_conflict'
    assert result.runtime_trace['grounding_validation'] == 'fail'


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
                    "tracking_number": "CH020000129135",
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


def test_runtime_result_allows_trusted_tracking_followup_with_unrelated_locked_fact():
    from app.services.ai_runtime.schemas import RuntimeAIProviderResult
    from app.services.tracking_fact_schema import hash_tracking_number
    from app.services.webchat_runtime_ai_service import _result_from_provider

    tracking_number = 'CH020000007813'
    provider_result = RuntimeAIProviderResult(
        ok=True,
        ai_generated=True,
        reply_source='private_ai_runtime',
        raw_provider='private_ai_runtime',
        raw_payload_safe_summary={'model': 'qwen2.5:3b', 'chat_mode': 'direct'},
        reply='Your parcel ending 007813 has been delivered. If the recipient cannot find it, please check with reception or the delivery contact point, then ask us for human review.',
        intent='tracking',
        tracking_number=tracking_number,
        handoff_required=False,
        handoff_reason=None,
        recommended_agent_action=None,
        elapsed_ms=9000,
    )

    result = _result_from_provider(
        provider_result,
        tracking_fact_metadata={
            'pii_redacted': True,
            'tool_status': 'success',
            'tracking_number_hash': hash_tracking_number(tracking_number),
            'tracking_number_suffix': '7813',
        },
        tracking_number=tracking_number,
        runtime_context={
            'knowledge_context': {
                'locked_facts': [
                    {
                        'item_key': 'nexus.support.customer.kb.ch.service.availability',
                        'answer': 'Switzerland domestic-to-domestic service is currently unavailable.',
                        'source': {'item_key': 'nexus.support.customer.kb.ch.service.availability'},
                    }
                ]
            }
        },
        body='The recipient says they did not receive it. What should we do?',
    )

    assert result.ok is True
    assert result.reply.startswith('Your parcel ending 007813')


def test_latency_class_uses_unified_runtime_for_customer_messages():
    from app.services.webchat_runtime_ai_service import _latency_class_for_request

    assert _latency_class_for_request(body='Please check CH020000007813', evidence_present=True) == 'trusted_tracking_fact'
    assert _latency_class_for_request(body='nigh', evidence_present=False) == 'unified_ai_runtime'
    assert _latency_class_for_request(body='你好', evidence_present=False) == 'unified_ai_runtime'
    assert _latency_class_for_request(body='hello latency smoke 1783325498843', evidence_present=False) == 'unified_ai_runtime'
    assert _latency_class_for_request(body='CH020000129135', evidence_present=False) == 'unified_ai_runtime'
    assert _latency_class_for_request(body='1783325498843', evidence_present=False) == 'unified_ai_runtime'
    assert _latency_class_for_request(body='tracking 1783325498843', evidence_present=False) == 'unified_ai_runtime'
    assert _latency_class_for_request(body='Please check CH020000007813', evidence_present=False) == 'unified_ai_runtime'
    assert _latency_class_for_request(body='瑞士本地到本地现在支持寄送吗？', evidence_present=False) == 'unified_ai_runtime'
