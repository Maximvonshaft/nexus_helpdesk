from __future__ import annotations

from app.services.webchat_query_service import admin_list_conversations_page


class FakeRole:
    admin = "admin"
    manager = "manager"
    auditor = "auditor"


class FakeUser:
    id = 1
    role = "admin"
    team_id = None


class FakeStatus:
    value = "pending_assignment"


class FakeConversation:
    def __init__(self, row_id: int) -> None:
        self.id = row_id
        self.public_id = f"wc_{row_id}"
        self.ticket_id = row_id
        self.visitor_name = "Visitor"
        self.visitor_email = None
        self.visitor_phone = None
        self.origin = "http://localhost"
        self.page_url = "http://localhost/demo"
        self.last_seen_at = None
        self.updated_at = None
        self.active_ai_turn_id = None
        self.active_ai_status = None
        self.active_ai_for_message_id = None
        self.active_ai_context_cutoff_message_id = None
        self.active_ai_started_at = None
        self.active_ai_updated_at = None


class FakeTicket:
    def __init__(self, row_id: int) -> None:
        self.id = row_id
        self.ticket_no = f"T-{row_id}"
        self.title = "Ticket"
        self.status = FakeStatus()
        self.conversation_state = "human_owned"
        self.required_action = None


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.limit_value = 50

    def join(self, *args, **kwargs):
        return self

    def outerjoin(self, *args, **kwargs):
        return self

    def group_by(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def all(self):
        return self.rows[: self.limit_value]

    def subquery(self):
        class Columns:
            conversation_id = object()
            last_message_id = object()
        class Subquery:
            c = Columns()
        return Subquery()


class FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def query(self, *args, **kwargs):
        return FakeQuery(self.rows)


def test_admin_list_conversations_page_returns_next_cursor(monkeypatch):
    rows = [
        (FakeConversation(3), FakeTicket(3), "text", None),
        (FakeConversation(2), FakeTicket(2), "text", None),
        (FakeConversation(1), FakeTicket(1), "text", None),
    ]
    monkeypatch.setattr("app.services.webchat_query_service.ensure_ticket_visible", lambda *args, **kwargs: None)
    result = admin_list_conversations_page(FakeDB(rows), FakeUser(), limit=2)

    assert len(result["items"]) == 2
    assert result["next_cursor"] == 1
