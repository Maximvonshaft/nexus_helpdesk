from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"replacement mismatch {path}: expected=1 actual={count}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


def remove_between(path: str, start: str, end: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    start_index = text.find(start)
    if start_index < 0:
        raise SystemExit(f"start marker missing {path}: {start!r}")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise SystemExit(f"end marker missing {path}: {end!r}")
    file.write_text(text[:start_index] + text[end_index:], encoding="utf-8")


replace_once(
    "backend/app/services/conversation_operator_service.py",
    "    return _message_payload(message)\n",
    '    return {"ok": True, **_message_payload(message)}\n',
)

voice_test = "backend/tests/test_webchat_voice_api.py"
replace_once(
    voice_test,
    "def test_public_create_voice_session_binds_conversation_without_ticket():\n",
    "def test_public_create_voice_session_lazily_binds_ticket_for_voice():\n",
)
replace_once(
    voice_test,
    '''        row = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.public_id == payload["voice_session_id"]).one()
        assert row.ticket_id is None
        assert row.provider == "mock"
        events = db.query(WebchatEvent).filter(
            WebchatEvent.conversation_id == row.conversation_id,
            WebchatEvent.ticket_id.is_(None),
        ).all()
''',
    '''        row = db.query(WebchatVoiceSession).filter(
            WebchatVoiceSession.public_id == payload["voice_session_id"]
        ).one()
        assert row.ticket_id is not None
        assert row.provider == "mock"
        conversation = db.get(WebchatConversation, row.conversation_id)
        assert conversation is not None
        assert conversation.ticket_id == row.ticket_id
        events = db.query(WebchatEvent).filter(
            WebchatEvent.conversation_id == row.conversation_id,
            WebchatEvent.ticket_id == row.ticket_id,
        ).all()
''',
)

compensation = "backend/tests/test_webchat_voice_room_compensation.py"
compensation_text = Path(compensation).read_text(encoding="utf-8")
if "canonical_list = client.get(" in compensation_text:
    remove_between(
        compensation,
        "    canonical_list = client.get(\n",
        '    return payload["conversation_id"], payload["visitor_token"]\n',
    )

print("PR 774 bounded backend patch applied")
