from __future__ import annotations

from types import SimpleNamespace

from app.models import Ticket
from app.services.operator_queue_presentation import project_unified_queue_display
from app.webchat_models import WebchatConversation


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_args):
        return self

    def all(self):
        return self._rows


class _Database:
    def __init__(self):
        self.queries: list[str] = []

    def query(self, *columns):
        model = getattr(columns[0], "class_", None)
        if model is Ticket:
            self.queries.append("tickets")
            return _Query([
                SimpleNamespace(id=11, ticket_no="T-11", title="Customer supplied title: helpdesk sync MCP CLI"),
            ])
        if model is WebchatConversation:
            self.queries.append("conversations")
            return _Query([
                SimpleNamespace(id=22, visitor_name="Montenegro Caller"),
            ])
        raise AssertionError(f"unexpected projection query: {columns!r}")


def _item(*, source_type: str, ticket_id: int | None, conversation_id: int | None):
    return {
        "source_type": source_type,
        "ticket_id": ticket_id,
        "conversation_id": conversation_id,
    }


def test_queue_display_projection_uses_business_identity_without_rewriting_text():
    db = _Database()
    result = {
        "items": [
            _item(source_type="ticket", ticket_id=11, conversation_id=None),
            _item(source_type="handoff", ticket_id=None, conversation_id=22),
            _item(source_type="dispatch", ticket_id=None, conversation_id=None),
        ]
    }

    projected = project_unified_queue_display(db, result)

    assert projected is result
    assert result["items"][0]["display_label"] == "T-11"
    assert result["items"][0]["display_summary"] == "Customer supplied title: helpdesk sync MCP CLI"
    assert result["items"][1]["display_label"] == "Montenegro Caller"
    assert result["items"][1]["display_summary"] == "客户实时会话"
    assert result["items"][2]["display_label"] == "内部任务"
    assert result["items"][2]["display_summary"] == "内部派发任务"
    assert db.queries == ["tickets", "conversations"]


def test_empty_queue_display_projection_performs_no_queries():
    db = _Database()
    result = {"items": []}

    assert project_unified_queue_display(db, result) is result
    assert db.queries == []
