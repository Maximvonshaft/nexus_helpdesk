from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/whatsapp_lite_session_dedupe_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api import whatsapp_lite as module  # noqa: E402


def test_recipient_from_turn_session_strips_turn_suffix() -> None:
    row = {
        "sessionKey": "agent:support:whatsapp:direct:+4915258445485:turn:3eb0abc",
        "channel": "whatsapp",
    }

    assert module._recipient_from_row(row) == "+4915258445485"


def test_conversation_list_collapses_turn_sessions_onto_base(monkeypatch) -> None:
    rows = [
        {
            "sessionKey": "agent:support:whatsapp:direct:+4915258445485",
            "channel": "whatsapp",
            "displayName": "Customer",
            "updatedAt": 1782184000000,
            "lastMessage": "base message",
        },
        {
            "sessionKey": "agent:support:whatsapp:direct:+4915258445485:turn:3eb0abc",
            "channel": "whatsapp",
            "displayName": "Customer",
            "updatedAt": 1782185000000,
            "lastMessage": "newer turn message",
        },
        {
            "sessionKey": "agent:support:whatsapp:direct:+38978303698:turn:3a5a",
            "channel": "whatsapp",
            "displayName": "Turn Only",
            "updatedAt": 1782184500000,
            "lastMessage": "turn only latest",
        },
    ]
    seen_limits: list[int] = []

    def fake_list(*, limit: int, channel: str):
        seen_limits.append(limit)
        assert channel == "whatsapp"
        return rows

    monkeypatch.setattr(module, "ensure_capability", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "list_openclaw_conversations", fake_list)

    page = module.list_whatsapp_lite_conversations(limit=30, db=None, current_user=object())

    assert seen_limits == [200]
    assert [item.recipient for item in page.items] == ["+4915258445485", "+38978303698"]
    assert page.items[0].session_key == "agent:support:whatsapp:direct:+4915258445485"
    assert page.items[0].latest_message == "newer turn message"
    assert page.items[1].session_key == "agent:support:whatsapp:direct:+38978303698:turn:3a5a"


def test_conversation_list_returns_one_row_per_whatsapp_peer(monkeypatch) -> None:
    rows = [
        {
            "sessionKey": f"agent:support:whatsapp:direct:+41712345678:turn:3a{idx}",
            "channel": "whatsapp",
            "updatedAt": 1782184000000 + idx,
            "lastMessage": f"message {idx}",
        }
        for idx in range(10)
    ]

    monkeypatch.setattr(module, "ensure_capability", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        module,
        "list_openclaw_conversations",
        lambda *, limit, channel: rows,
    )

    page = module.list_whatsapp_lite_conversations(limit=30, db=None, current_user=object())

    assert len(page.items) == 1
    assert page.items[0].recipient == "+41712345678"
    assert page.items[0].latest_message == "message 9"


def test_conversation_list_supports_search_and_cursor(monkeypatch) -> None:
    rows = [
        {
            "sessionKey": f"agent:support:whatsapp:direct:+49152584454{idx}",
            "channel": "whatsapp",
            "displayName": f"Customer {idx}",
            "updatedAt": 1782184000000 + idx,
            "lastMessage": "address change needed" if idx != 1 else "delivery follow up",
        }
        for idx in range(4)
    ]
    seen_limits: list[int] = []

    def fake_list(*, limit: int, channel: str):
        seen_limits.append(limit)
        assert channel == "whatsapp"
        return rows

    monkeypatch.setattr(module, "ensure_capability", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "list_openclaw_conversations", fake_list)

    first_page = module.list_whatsapp_lite_conversations(
        limit=1,
        cursor=None,
        q="address",
        db=None,
        current_user=object(),
    )
    second_page = module.list_whatsapp_lite_conversations(
        limit=1,
        cursor=first_page.next_cursor,
        q="address",
        db=None,
        current_user=object(),
    )

    assert seen_limits == [200, 200]
    assert first_page.total_visible == 3
    assert first_page.next_cursor == "1"
    assert first_page.items[0].latest_message == "address change needed"
    assert second_page.next_cursor == "2"
    assert second_page.items[0].recipient != first_page.items[0].recipient


def test_conversation_detail_merges_base_turn_and_outbox_messages(monkeypatch) -> None:
    base_key = "agent:support:whatsapp:direct:+4915258445485"
    turn_key = f"{base_key}:turn:3eb0abc"
    rows = [
        {
            "sessionKey": base_key,
            "channel": "whatsapp",
            "updatedAt": 1782184000000,
        },
        {
            "sessionKey": turn_key,
            "channel": "whatsapp",
            "updatedAt": 1782185000000,
        },
    ]

    def fake_list(*, limit: int, channel: str):
        assert channel == "whatsapp"
        return rows

    def fake_read(session_key: str, limit: int):
        conversation = {"sessionKey": session_key, "channel": "whatsapp"}
        if session_key == base_key:
            return conversation, [
                {"role": "user", "content": "base customer", "timestamp": 1782184000000},
            ]
        if session_key == turn_key:
            return conversation, [
                {"role": "user", "content": "turn customer", "timestamp": 1782185000000},
            ]
        raise AssertionError(f"unexpected session key: {session_key}")

    def fake_outbox(session_key: str, *, limit: int = 200):
        if session_key != base_key:
            return []
        return [
            module.WhatsAppLiteMessage(
                id="outbox-1",
                author="human",
                body="console reply",
                timestamp="2026-06-26T07:00:01+00:00",
            )
        ]

    monkeypatch.setattr(module, "ensure_capability", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "list_openclaw_conversations", fake_list)
    monkeypatch.setattr(module, "read_openclaw_bridge_conversation", fake_read)
    monkeypatch.setattr(module, "_read_outbox_mirror", fake_outbox)
    monkeypatch.setattr(module, "fetch_openclaw_bridge_attachments", lambda *args, **kwargs: [])

    detail = module.get_whatsapp_lite_conversation(
        session_key=base_key,
        limit=100,
        db=None,
        current_user=object(),
    )

    assert [message.body for message in detail.messages] == [
        "base customer",
        "turn customer",
        "console reply",
    ]
    assert detail.conversation.latest_message == "console reply"
