from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.enums import UserRole
from app.main import app
from app.models import BackgroundJob, User
from app.services.background_jobs import WEBCHAT_AI_REPLY_JOB, dispatch_pending_background_jobs
from app.utils.time import utc_now
from app.webchat_models import WebchatAITurn, WebchatConversation, WebchatEvent, WebchatMessage  # noqa: F401


def _ensure_schema_and_user() -> None:
    Base.metadata.create_all(bind=engine)
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
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)

    first = _send(client, conversation_id, visitor_token, 'Old question', 'turn-runtime-stale-1')
    first_turn_id = first['ai_turn_id']

    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        turn = db.query(WebchatAITurn).filter(WebchatAITurn.id == first_turn_id).first()
        first_message = db.query(WebchatMessage).filter(WebchatMessage.client_message_id == 'turn-runtime-stale-1').first()
        assert conversation is not None and turn is not None and first_message is not None
        turn.status = 'bridge_calling'
        turn.context_cutoff_message_id = first_message.id
        conversation.active_ai_status = 'bridge_calling'
        conversation.active_ai_context_cutoff_message_id = first_message.id
        db.commit()
    finally:
        db.close()

    _send(client, conversation_id, visitor_token, 'Newer question', 'turn-runtime-stale-2')

    from app.services import webchat_ai_safe_service
    monkeypatch.setattr(webchat_ai_safe_service.settings, 'webchat_ai_auto_reply_mode', 'safe_ack')

    db = SessionLocal()
    try:
        first_message = db.query(WebchatMessage).filter(WebchatMessage.client_message_id == 'turn-runtime-stale-1').first()
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
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)

    sent = _send(client, conversation_id, visitor_token, 'Hello', 'turn-runtime-dispatch-1')
    ai_turn_id = sent['ai_turn_id']

    from app.services import webchat_ai_safe_service
    monkeypatch.setattr(webchat_ai_safe_service.settings, 'webchat_ai_auto_reply_mode', 'safe_ack')

    db = SessionLocal()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.dedupe_key == f'webchat-ai-turn:{ai_turn_id}').first()
        assert job is not None
        job.next_run_at = None
        db.commit()
        processed = dispatch_pending_background_jobs(db, worker_id='turn-runtime-worker')
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
    agent_messages = [msg for msg in payload['messages'] if msg['author_label'] == 'NexusDesk AI Assistant']
    assert agent_messages
    assert agent_messages[0].get('ai_turn_id') == ai_turn_id

    db = SessionLocal()
    try:
        turn = db.query(WebchatAITurn).filter(WebchatAITurn.id == ai_turn_id).first()
        assert turn is not None
        assert turn.status == 'completed'
        assert turn.reply_message_id is not None
        assert db.query(WebchatMessage).filter(WebchatMessage.ai_turn_id == ai_turn_id, WebchatMessage.direction == 'agent').count() == 1
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
