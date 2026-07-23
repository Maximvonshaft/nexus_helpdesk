from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.enums import JobStatus
from app.main import app
from app.models import BackgroundJob
from app.services import background_jobs, webchat_ai_orchestration_service, webchat_ai_service
from app.services.agent_runtime.terminal_reply import customer_visible_fallback
from app.services.background_job_transaction_boundary import (
    _finalize_dead_webchat_ai_job,
)
from app.services.background_jobs import dispatch_pending_webchat_ai_reply_jobs
from app.webchat_models import WebchatAITurn, WebchatConversation, WebchatMessage


def _init_and_send(body: str) -> tuple[str, str]:
    Base.metadata.create_all(bind=engine)
    client = TestClient(app)
    key = uuid.uuid4().hex
    init = client.post(
        "/api/webchat/init",
        json={
            "tenant_key": f"terminal-{key}",
            "channel_key": "website",
            "visitor_name": "Terminal Fallback Visitor",
            "origin": "https://example.test",
            "page_url": "https://example.test/help",
        },
    )
    assert init.status_code == 200, init.text
    initialized = init.json()
    sent = client.post(
        f"/api/webchat/conversations/{initialized['conversation_id']}/messages",
        headers={"X-Webchat-Visitor-Token": initialized["visitor_token"]},
        json={"body": body, "client_message_id": f"terminal-{key}"},
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["ai_pending"] is True
    return initialized["conversation_id"], initialized["visitor_token"]


def _rows(public_id: str):
    db = SessionLocal()
    conversation = (
        db.query(WebchatConversation)
        .filter(WebchatConversation.public_id == public_id)
        .one()
    )
    turn = (
        db.query(WebchatAITurn)
        .filter(WebchatAITurn.conversation_id == conversation.id)
        .order_by(WebchatAITurn.id.desc())
        .one()
    )
    job = db.get(BackgroundJob, turn.job_id)
    assert job is not None
    return db, conversation, turn, job


def test_terminal_fallback_authority_supports_portuguese():
    body = customer_visible_fallback("pt-BR", "Onde está o meu pacote?")
    assert body == (
        "Desculpe, não consegui concluir este atendimento agora. "
        "Tente novamente ou peça atendimento humano."
    )
    assert "provider" not in body.lower()
    assert "runtime" not in body.lower()


def test_exhausted_webchat_ai_job_commits_one_safe_ticketless_terminal_outcome(
    monkeypatch,
):
    monkeypatch.setattr(
        webchat_ai_orchestration_service.settings,
        "webchat_ai_auto_reply_mode",
        "runtime",
    )

    def fail_runtime(**_kwargs):
        raise RuntimeError("private-provider-secret-should-never-be-public")

    monkeypatch.setattr(webchat_ai_service, "_run_runtime_reply_sync", fail_runtime)
    public_id, visitor_token = _init_and_send("Onde está o meu pacote?")

    db, conversation, turn, job = _rows(public_id)
    try:
        job.attempt_count = job.max_attempts - 1
        job.next_run_at = None
        db.commit()
        processed = dispatch_pending_webchat_ai_reply_jobs(
            db,
            worker_id="terminal-fallback-test",
        )
        assert [row.id for row in processed] == [job.id]
        db.commit()

        db.refresh(job)
        db.refresh(turn)
        assert job.status == JobStatus.dead
        assert job.last_error == "webchat_ai_attempts_exhausted"
        assert turn.status == "completed"
        assert turn.reply_source == "agent_runtime:fallback"
        assert turn.fallback_reason == "background_job_exhausted"

        messages = (
            db.query(WebchatMessage)
            .filter(
                WebchatMessage.conversation_id == conversation.id,
                WebchatMessage.direction == "agent",
                WebchatMessage.ai_turn_id == turn.id,
            )
            .all()
        )
        assert len(messages) == 1
        assert messages[0].body == customer_visible_fallback(
            "pt",
            "Onde está o meu pacote?",
        )
        assert "private-provider-secret" not in messages[0].body
        assert "RuntimeError" not in messages[0].body

        _finalize_dead_webchat_ai_job(db, job)
        db.commit()
        assert (
            db.query(WebchatMessage)
            .filter(
                WebchatMessage.conversation_id == conversation.id,
                WebchatMessage.direction == "agent",
                WebchatMessage.ai_turn_id == turn.id,
            )
            .count()
            == 1
        )
    finally:
        db.close()

    client = TestClient(app)
    poll = client.get(
        f"/api/webchat/conversations/{public_id}/messages",
        headers={"X-Webchat-Visitor-Token": visitor_token},
    )
    assert poll.status_code == 200, poll.text
    public_bodies = [
        item["body"]
        for item in poll.json()["messages"]
        if item["direction"] == "agent"
    ]
    assert public_bodies == [
        customer_visible_fallback("pt", "Onde está o meu pacote?")
    ]


def test_committed_handoff_suppresses_dead_job_terminal_fallback(monkeypatch):
    monkeypatch.setattr(
        webchat_ai_orchestration_service.settings,
        "webchat_ai_auto_reply_mode",
        "runtime",
    )
    public_id, _visitor_token = _init_and_send("Please connect me to a person")
    db, conversation, turn, job = _rows(public_id)
    try:
        conversation.ai_suspended = True
        conversation.handoff_status = "requested"
        turn.status = "failed"
        job.status = JobStatus.dead
        job.attempt_count = job.max_attempts
        db.commit()

        _finalize_dead_webchat_ai_job(db, job)
        db.commit()
        db.refresh(turn)
        assert turn.status == "superseded"
        assert turn.status_reason == "handoff_started_before_terminal_fallback"
        assert (
            db.query(WebchatMessage)
            .filter(
                WebchatMessage.conversation_id == conversation.id,
                WebchatMessage.direction == "agent",
                WebchatMessage.ai_turn_id == turn.id,
            )
            .count()
            == 0
        )
    finally:
        db.close()
