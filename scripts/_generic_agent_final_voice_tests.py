from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text.rstrip() + "\n", encoding="utf-8")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def function_bounds(text: str, name: str) -> tuple[int, int]:
    match = re.search(rf"^def {re.escape(name)}\(", text, flags=re.MULTILINE)
    if match is None:
        raise SystemExit(f"function not found: {name}")
    next_match = re.search(r"^def [A-Za-z0-9_]+\(", text[match.end():], flags=re.MULTILINE)
    end = len(text) if next_match is None else match.end() + next_match.start()
    return match.start(), end


def replace_function(text: str, name: str, replacement: str) -> str:
    start, end = function_bounds(text, name)
    return text[:start].rstrip() + "\n\n\n" + replacement.strip() + "\n\n\n" + text[end:].lstrip("\n")


def remove_function(text: str, name: str) -> str:
    start, end = function_bounds(text, name)
    return text[:start].rstrip() + "\n\n\n" + text[end:].lstrip("\n")


route_replacements = (
    (r'f"/api/webchat/admin/tickets/\{ticket_id\}/voice/\{voice_session_id\}', 'f"/api/webchat/admin/voice/{voice_session_id}'),
    (r'"/admin/tickets/\{ticket_id\}/voice/\{voice_session_id\}', '"/admin/voice/{voice_session_id}'),
)
for root in (ROOT / "backend" / "tests", ROOT / "docs", ROOT / "scripts"):
    if not root.exists():
        continue
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".md", ".sh", ".js", ".ts", ".tsx"}:
            continue
        text = path.read_text(encoding="utf-8")
        original = text
        for pattern, replacement in route_replacements:
            text = re.sub(pattern, replacement, text)
        if text != original:
            path.write_text(text, encoding="utf-8")

legacy_voice_test = ROOT / "backend/tests/test_ticketless_voice_ticket_binding.py"
if legacy_voice_test.exists():
    legacy_voice_test.unlink()

voice_api_test_path = "backend/tests/test_webchat_voice_api.py"
voice_api_tests = read(voice_api_test_path)
voice_api_tests = voice_api_tests.replace(
    "def test_public_create_voice_session_lazily_binds_canonical_ticket():",
    "def test_public_create_voice_session_remains_ticketless():",
)
voice_api_tests = voice_api_tests.replace(
    "        assert row.ticket_id is not None\n"
    "        assert conversation.ticket_id == row.ticket_id\n",
    "        assert row.ticket_id is None\n"
    "        assert conversation.ticket_id is None\n",
    1,
)
voice_api_tests = voice_api_tests.replace(
    "                WebchatEvent.ticket_id == row.ticket_id,\n",
    "                WebchatEvent.ticket_id.is_(None),\n",
    1,
)
if "test_ticketless_session_can_be_accepted_and_ended_without_ticket_creation" not in voice_api_tests:
    insertion = '''


def test_ticketless_session_can_be_accepted_and_ended_without_ticket_creation():
    client = TestClient(app)
    conversation_id, visitor_token, ticket_id = _create_webchat_conversation(
        client,
        name="Ticketless Voice Visitor",
        create_ticket=False,
    )
    assert ticket_id is None
    created = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={},
    )
    assert created.status_code == 200, created.text
    voice_session_id = created.json()["voice_session_id"]

    accepted = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/accept",
        headers=_admin_headers(9202),
    )
    assert accepted.status_code == 200, accepted.text
    ended = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/end",
        headers=_admin_headers(9202),
    )
    assert ended.status_code == 200, ended.text

    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(
            WebchatConversation.public_id == conversation_id
        ).one()
        session = db.query(WebchatVoiceSession).filter(
            WebchatVoiceSession.public_id == voice_session_id
        ).one()
        assert conversation.ticket_id is None
        assert session.ticket_id is None
        final_message = db.query(WebchatMessage).filter(
            WebchatMessage.conversation_id == conversation.id,
            WebchatMessage.client_message_id == f"voice-call-ended:{voice_session_id}",
        ).one()
        assert final_message.ticket_id is None
    finally:
        db.close()
'''
    marker = "def test_public_create_voice_session_rejects_invalid_token():"
    pos = voice_api_tests.find(marker)
    if pos < 0:
        raise SystemExit("voice API test insertion marker missing")
    voice_api_tests = voice_api_tests[:pos] + insertion + "\n\n" + voice_api_tests[pos:]
write(voice_api_test_path, voice_api_tests)

voice_static_path = "backend/tests/test_webchat_voice_p0_static.py"
voice_static = read(voice_static_path)
voice_static = voice_static.replace(
    'assert \'"/admin/tickets/{ticket_id}/voice/{voice_session_id}/reject"\' in api',
    'assert \'"/admin/voice/{voice_session_id}/reject"\' in api\n'
    '    assert "ensure_voice_ticket_for_public_conversation" not in api',
)
write(voice_static_path, voice_static)
