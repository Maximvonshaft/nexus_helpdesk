from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_polling_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.webchat import _hash_token, _validate_public_conversation_token  # noqa: E402
from app.services.webchat_performance import list_public_messages_throttled  # noqa: E402
from app.utils.time import utc_now  # noqa: E402


class FakeColumn:
    def __eq__(self, other):
        return self

    def __gt__(self, other):
        return self

    def asc(self):
        return self


class FakeMessageModel:
    id = FakeColumn()
    conversation_id = FakeColumn()


class FakeMessage:
    def __init__(self, message_id: int, body: str = "hello") -> None:
        self.id = message_id
        self.direction = "visitor"
        self.body = body
        self.body_text = body
        self.message_type = "text"
        self.payload_json = None
        self.metadata_json = None
        self.client_message_id = None
        self.ai_turn_id = None
        self.delivery_status = "sent"
        self.action_status = None
        self.author_label = "Visitor"
        self.created_at = None


class FakeConversation:
    def __init__(self, *, last_seen_at) -> None:
        self.id = 1
        self.public_id = "wc_test"
        self.status = "open"
        self.last_seen_at = last_seen_at
        self.updated_at = last_seen_at
        self.visitor_token_hash = _hash_token("good-token")
        self.visitor_token_expires_at = utc_now() + timedelta(days=1)


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.limit_value = 50

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def all(self):
        return self.rows[: self.limit_value]


class FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.flush_count = 0

    def query(self, model):
        return FakeQuery(self.rows)

    def flush(self):
        self.flush_count += 1


def test_repeated_polls_do_not_touch_last_seen_within_interval(monkeypatch):
    monkeypatch.setattr("app.services.webchat_performance.WebchatMessage", FakeMessageModel)
    monkeypatch.setenv("WEBCHAT_LAST_SEEN_WRITE_INTERVAL_SECONDS", "60")
    db = FakeDB([FakeMessage(1)])
    conversation = FakeConversation(last_seen_at=utc_now())

    for _ in range(10):
        result = list_public_messages_throttled(db, conversation, after_id=0, limit=50)
        assert result["last_seen_touched"] is False

    assert db.flush_count == 0


def test_poll_touches_last_seen_after_interval(monkeypatch):
    monkeypatch.setattr("app.services.webchat_performance.WebchatMessage", FakeMessageModel)
    monkeypatch.setenv("WEBCHAT_LAST_SEEN_WRITE_INTERVAL_SECONDS", "60")
    db = FakeDB([FakeMessage(1), FakeMessage(2)])
    conversation = FakeConversation(last_seen_at=utc_now() - timedelta(seconds=61))

    result = list_public_messages_throttled(db, conversation, after_id=0, limit=1)

    assert result["last_seen_touched"] is True
    assert db.flush_count == 1
    assert len(result["messages"]) == 1
    assert result["has_more"] is True
    assert result["next_after_id"] == 1


def test_poll_response_shape_is_compatible(monkeypatch):
    monkeypatch.setattr("app.services.webchat_performance.WebchatMessage", FakeMessageModel)
    db = FakeDB([FakeMessage(7, "body")])
    conversation = FakeConversation(last_seen_at=utc_now())

    result = list_public_messages_throttled(db, conversation, after_id=0, limit=50)

    assert result["conversation_id"] == "wc_test"
    assert result["status"] == "open"
    assert result["messages"][0]["id"] == 7
    assert result["messages"][0]["body"] == "body"
    assert result["next_after_id"] == 7


def test_token_mismatch_still_fails():
    conversation = FakeConversation(last_seen_at=utc_now())

    with pytest.raises(HTTPException) as exc:
        _validate_public_conversation_token(conversation, "wrong-token")

    assert exc.value.status_code == 403
