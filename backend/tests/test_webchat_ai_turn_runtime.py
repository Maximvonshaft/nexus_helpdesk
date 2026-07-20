from __future__ import annotations

import json
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.db import Base, SessionLocal, engine
from app.enums import UserRole
from app.main import app
from app.models import BackgroundJob, User
from app.services.background_jobs import WEBCHAT_AI_REPLY_JOB
from app.services.webchat_runtime_ai_service import WebchatRuntimeReplyResult
from app.utils.time import utc_now
from app.webchat_models import WebchatAITurn, WebchatConversation, WebchatEvent, WebchatMessage


def _ensure_schema_and_user() -> None:
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        columns = {col["name"] for col in inspect(conn).get_columns("webchat_ai_turns")}
        if "runtime_trace_json" not in columns:
            conn.execute(text("ALTER TABLE webchat_ai_turns ADD COLUMN runtime_trace_json TEXT"))
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.id == 98765).first():
            db.add(
                User(
                    id=98765,
                    username="webchat_ai_turn_admin",
                    display_name="WebChat AI Turn Admin",
                    password_hash="test",
                    role=UserRole.admin,
                    is_active=True,
                )
            )
            db.commit()
    finally:
        db.close()


def _init_conversation(client: TestClient):
    response = client.post(
        "/api/webchat/init",
        json={
            "tenant_key": "turn-runtime-pytest",
            "channel_key": "website",
            "visitor_name": "Turn Runtime Visitor",
            "origin": "https://example.test",
            "page_url": "https://example.test/help",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    return payload["conversation_id"], payload["visitor_token"]


def _send(client: TestClient, conversation_id: str, visitor_token: str, body: str, client_message_id: str):
    response = client.post(
        f"/api/webchat/conversations/{conversation_id}/messages",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={"body": body, "client_message_id": client_message_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _clear_webchat_ai_jobs() -> None:
    db = SessionLocal()
    try:
        job_ids = [
            row[0]
            for row in db.query(BackgroundJob.id)
            .filter(BackgroundJob.job_type == WEBCHAT_AI_REPLY_JOB)
            .all()
        ]
        if job_ids:
            db.query(WebchatAITurn).filter(WebchatAITurn.job_id.in_(job_ids)).update(
                {WebchatAITurn.job_id: None},
                synchronize_session=False,
            )
        db.query(BackgroundJob).filter(BackgroundJob.job_type == WEBCHAT_AI_REPLY_JOB).delete(
            synchronize_session=False
        )
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
                {"tool_name": "knowledge.search", "ok": True, "status": "success"}
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

    sent = _send(client, conversation_id, visitor_token, "Where is my parcel?", "turn-runtime-1")
    assert sent["ai_pending"] is True
    assert sent["ai_status"] == "queued"
    assert sent["ai_turn_id"]

    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        turns = db.query(WebchatAITurn).filter(WebchatAITurn.conversation_id == conversation.id).all()
        assert len(turns) == 1
        assert turns[0].status == "queued"
        assert conversation.active_ai_turn_id == turns[0].id
        assert (
            db.query(BackgroundJob)
            .filter(
                BackgroundJob.job_type == WEBCHAT_AI_REPLY_JOB,
                BackgroundJob.dedupe_key == f"webchat-ai-turn:{turns[0].id}",
            )
            .count()
            == 1
        )
    finally:
        db.close()

    polled = client.get(
        f"/api/webchat/conversations/{conversation_id}/messages",
        headers={"X-Webchat-Visitor-Token": visitor_token},
    )
    assert polled.status_code == 200, polled.text
    assert polled.json()["ai_pending"] is True


def test_duplicate_client_message_id_is_idempotent():
    _ensure_schema_and_user()
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)

    first = _send(client, conversation_id, visitor_token, "Hello once", "turn-runtime-idem-1")
    second = _send(client, conversation_id, visitor_token, "Hello once", "turn-runtime-idem-1")

    assert second["idempotent"] is True
    assert second["message"]["id"] == first["message"]["id"]


def test_queued_turn_coalesces_consecutive_visitor_messages():
    _ensure_schema_and_user()
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)

    first = _send(client, conversation_id, visitor_token, "Where is my parcel?", "turn-runtime-coalesce-1")
    second = _send(
        client,
        conversation_id,
        visitor_token,
        "Tracking number is ABC1234567",
        "turn-runtime-coalesce-2",
    )

    assert first["ai_turn_id"] == second["ai_turn_id"]
    assert second["coalesced"] is True


def test_processing_turn_queues_next_turn_for_new_message():
    _ensure_schema_and_user()
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)
    first = _send(client, conversation_id, visitor_token, "First question", "turn-runtime-next-1")

    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        turn = db.get(WebchatAITurn, first["ai_turn_id"])
        turn.status = "bridge_calling"
        turn.context_cutoff_message_id = turn.latest_visitor_message_id or turn.trigger_message_id
        conversation.active_ai_status = "bridge_calling"
        conversation.active_ai_context_cutoff_message_id = turn.context_cutoff_message_id
        db.commit()
    finally:
        db.close()

    second = _send(client, conversation_id, visitor_token, "Second question", "turn-runtime-next-2")
    assert second["ai_turn_id"] == first["ai_turn_id"]
    assert second["next_ai_turn_id"]


def test_stale_turn_is_superseded_before_agent_reply(monkeypatch):
    _ensure_schema_and_user()
    from app.services import webchat_ai_orchestration_service

    monkeypatch.setattr(webchat_ai_orchestration_service.settings, "webchat_ai_auto_reply_mode", "runtime")
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)
    first = _send(client, conversation_id, visitor_token, "Old question", "turn-runtime-stale-1")

    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        first_message = db.get(WebchatMessage, first["message"]["id"])
        turn = db.get(WebchatAITurn, first["ai_turn_id"])
        turn.status = "bridge_calling"
        turn.context_cutoff_message_id = first_message.id
        conversation.active_ai_status = "bridge_calling"
        conversation.active_ai_context_cutoff_message_id = first_message.id
        db.commit()
    finally:
        db.close()

    _send(client, conversation_id, visitor_token, "Newer question", "turn-runtime-stale-2")

    db = SessionLocal()
    try:
        first_message = db.get(WebchatMessage, first["message"]["id"])
        result = webchat_ai_orchestration_service.process_webchat_ai_reply_job(
            db,
            conversation_id=first_message.conversation_id,
            ticket_id=first_message.ticket_id,
            visitor_message_id=first_message.id,
        )
        db.commit()
        assert result["status"] == "superseded"
        assert (
            db.query(WebchatMessage)
            .filter(
                WebchatMessage.ai_turn_id == first["ai_turn_id"],
                WebchatMessage.direction == "agent",
            )
            .count()
            == 0
        )
    finally:
        db.close()


def test_agent_runtime_failure_produces_customer_visible_fallback(monkeypatch):
    _ensure_schema_and_user()
    from app.services import webchat_ai_orchestration_service, webchat_ai_service

    monkeypatch.setattr(webchat_ai_orchestration_service.settings, "webchat_ai_auto_reply_mode", "runtime")
    monkeypatch.setattr(
        webchat_ai_service,
        "_run_runtime_reply_sync",
        lambda **kwargs: WebchatRuntimeReplyResult(
            ok=True,
            ai_generated=False,
            reply_source="agent_runtime:fallback",
            reply="Sorry, I could not complete that request right now. Please try again or ask for human support.",
            intent="runtime_unavailable",
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            elapsed_ms=5,
            error_code="all_providers_failed",
            retry_after_ms=1500,
            runtime_trace={"agent_runtime": True, "error_code": "all_providers_failed"},
            tool_calls=[],
        ),
    )
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)
    sent = _send(client, conversation_id, visitor_token, "Please help", "turn-runtime-fallback-1")

    db = SessionLocal()
    try:
        visitor_message = db.get(WebchatMessage, sent["message"]["id"])
        result = webchat_ai_orchestration_service.process_webchat_ai_reply_job(
            db,
            conversation_id=visitor_message.conversation_id,
            ticket_id=visitor_message.ticket_id,
            visitor_message_id=visitor_message.id,
        )
        db.commit()
        assert result["status"] == "done"
        reply = (
            db.query(WebchatMessage)
            .filter(
                WebchatMessage.conversation_id == visitor_message.conversation_id,
                WebchatMessage.direction == "agent",
                WebchatMessage.ai_turn_id == sent["ai_turn_id"],
            )
            .first()
        )
        assert reply is not None
        assert "could not complete" in reply.body
    finally:
        db.close()


def test_reconciler_times_out_stale_bridge_calling_turn():
    _ensure_schema_and_user()
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)
    sent = _send(client, conversation_id, visitor_token, "Timeout please", "turn-runtime-timeout-1")

    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        turn = db.get(WebchatAITurn, sent["ai_turn_id"])
        old = utc_now() - timedelta(seconds=3600)
        turn.status = "bridge_calling"
        turn.updated_at = old
        turn.started_at = old
        conversation.active_ai_status = "bridge_calling"
        conversation.active_ai_turn_id = turn.id
        conversation.active_ai_updated_at = old
        db.commit()
    finally:
        db.close()

    polled = client.get(
        f"/api/webchat/conversations/{conversation_id}/messages",
        headers={"X-Webchat-Visitor-Token": visitor_token},
    )
    assert polled.status_code == 200, polled.text
    assert polled.json()["ai_pending"] is False

    db = SessionLocal()
    try:
        turn = db.get(WebchatAITurn, sent["ai_turn_id"])
        assert turn.status == "timeout"
        assert (
            db.query(WebchatEvent)
            .filter(
                WebchatEvent.conversation_id == turn.conversation_id,
                WebchatEvent.event_type == "ai_turn.timeout",
            )
            .count()
            >= 1
        )
    finally:
        db.close()


def test_failed_turn_runtime_trace_is_redacted():
    from app.services.webchat_ai_turn_service import complete_ai_turn_with_reply

    _ensure_schema_and_user()
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client)
    sent = _send(client, conversation_id, visitor_token, "Trace test", "turn-runtime-trace-1")

    db = SessionLocal()
    try:
        turn = db.get(WebchatAITurn, sent["ai_turn_id"])
        conversation = db.get(WebchatConversation, turn.conversation_id)
        complete_ai_turn_with_reply(
            db,
            conversation=conversation,
            turn=turn,
            result={
                "status": "failed_no_public_reply",
                "reason": "provider_unavailable",
                "reply_source": "private_ai_runtime",
                "bridge_elapsed_ms": 9309,
                "runtime_trace": {
                    "model": "test-model",
                    "raw_identifier": "CH020000129135",
                    "authorization": ("Bear" + "er ") + "SHOULD_NOT_PERSIST",
                },
            },
        )
        db.commit()
        db.refresh(turn)
        trace = json.loads(turn.runtime_trace_json or "{}")
        trace_text = json.dumps(trace, ensure_ascii=False)
        assert trace["model"] == "test-model"
        assert "CH020000129135" not in trace_text
        assert "SHOULD_NOT_PERSIST" not in trace_text
    finally:
        db.close()


def teardown_module():
    _clear_webchat_ai_jobs()
