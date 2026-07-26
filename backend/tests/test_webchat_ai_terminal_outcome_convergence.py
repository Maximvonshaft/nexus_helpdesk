from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.enums import JobStatus
from app.main import app
from app.models import BackgroundJob
from app.services import background_jobs, webchat_ai_orchestration_service
from app.services.agent_runtime.terminal_reply import customer_visible_fallback
from app.services.webchat_ai_orchestration_service import (
    WebchatAITerminalOutcomeRequired,
    _require_customer_terminal_outcome,
)
from app.services.webchat_ai_reconciler import reconcile_webchat_ai_state
from app.utils.time import utc_now
from app.webchat_models import WebchatAITurn, WebchatConversation, WebchatMessage


def _init_and_send(body: str) -> tuple[str, str, int]:
    Base.metadata.create_all(bind=engine)
    client = TestClient(app)
    key = uuid.uuid4().hex
    initialized = client.post(
        "/api/webchat/init",
        json={
            "tenant_key": f"terminal-convergence-{key}",
            "channel_key": "website",
            "visitor_name": "Terminal Convergence Visitor",
            "origin": "https://example.test",
            "page_url": "https://example.test/help",
        },
    )
    assert initialized.status_code == 200, initialized.text
    session = initialized.json()
    sent = client.post(
        f"/api/webchat/conversations/{session['conversation_id']}/messages",
        headers={"X-Webchat-Visitor-Token": session["visitor_token"]},
        json={"body": body, "client_message_id": f"terminal-{key}"},
    )
    assert sent.status_code == 200, sent.text
    payload = sent.json()
    assert payload["ai_pending"] is True
    return session["conversation_id"], session["visitor_token"], payload["ai_turn_id"]


def _rows(public_id: str, turn_id: int):
    db = SessionLocal()
    conversation = (
        db.query(WebchatConversation)
        .filter(WebchatConversation.public_id == public_id)
        .one()
    )
    turn = db.get(WebchatAITurn, turn_id)
    assert turn is not None
    job = db.get(BackgroundJob, turn.job_id)
    assert job is not None
    return db, conversation, turn, job


@pytest.mark.parametrize(
    "status",
    ["null_reply", "review_required", "failed_no_public_reply"],
)
def test_no_public_outcome_statuses_are_retryable_not_success(status: str):
    with pytest.raises(WebchatAITerminalOutcomeRequired) as exc:
        _require_customer_terminal_outcome(
            {"status": status, "reason": "private provider detail"}
        )
    text = str(exc.value)
    assert text.startswith(f"webchat_ai_customer_outcome_required:{status}:")
    assert "private provider detail" not in text


def test_completed_and_intentionally_superseded_results_do_not_retry():
    _require_customer_terminal_outcome({"status": "done", "message_id": 1})
    _require_customer_terminal_outcome(
        {"status": "superseded", "reason": "newer_message_before_reply"}
    )


def test_no_public_runtime_result_exhausts_into_one_canonical_fallback(monkeypatch):
    monkeypatch.setattr(
        webchat_ai_orchestration_service.settings,
        "webchat_ai_auto_reply_mode",
        "runtime",
    )
    monkeypatch.setattr(
        webchat_ai_orchestration_service,
        "_run_agent_reply",
        lambda *_args, **_kwargs: {
            "status": "failed_no_public_reply",
            "reason": "all_providers_failed",
            "runtime_trace": {"authorization": "Bearer SHOULD_NOT_PERSIST"},
        },
    )
    public_id, visitor_token, turn_id = _init_and_send("Where is my package?")
    db, conversation, turn, job = _rows(public_id, turn_id)
    try:
        job.max_attempts = 1
        job.next_run_at = None
        db.commit()
        processed = background_jobs.dispatch_pending_webchat_ai_reply_jobs(
            db,
            worker_id="terminal-convergence-worker",
        )
        assert [row.id for row in processed] == [job.id]
        db.commit()
        db.refresh(job)
        db.refresh(turn)
        assert job.status == JobStatus.dead
        assert turn.status == "completed"
        assert turn.reply_source == "agent_runtime:fallback"
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
            "en",
            "Where is my package?",
        )
        assert "SHOULD_NOT_PERSIST" not in messages[0].body
    finally:
        db.close()

    client = TestClient(app)
    polled = client.get(
        f"/api/webchat/conversations/{public_id}/messages",
        headers={"X-Webchat-Visitor-Token": visitor_token},
    )
    assert polled.status_code == 200, polled.text
    public_messages = [
        item
        for item in polled.json()["messages"]
        if item["direction"] == "agent"
    ]
    assert len(public_messages) == 1


def test_watchdog_timeout_uses_same_idempotent_terminal_outcome(monkeypatch):
    monkeypatch.setattr(
        webchat_ai_orchestration_service.settings,
        "webchat_ai_auto_reply_mode",
        "runtime",
    )
    public_id, _visitor_token, turn_id = _init_and_send("Please help me")
    db, conversation, turn, job = _rows(public_id, turn_id)
    try:
        turn.status = "bridge_calling"
        turn.updated_at = utc_now() - timedelta(minutes=10)
        conversation.active_ai_status = "bridge_calling"
        conversation.active_ai_updated_at = turn.updated_at
        job.status = JobStatus.processing
        job.locked_at = turn.updated_at
        db.commit()

        result = reconcile_webchat_ai_state(db, conversation_id=conversation.id)
        db.commit()
        db.refresh(turn)
        assert result["timed_out"] == 1
        assert result["recovered"] == 1
        assert turn.status == "completed"
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

        # Reconciliation is idempotent and may not create a second public result.
        reconcile_webchat_ai_state(db, conversation_id=conversation.id)
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
