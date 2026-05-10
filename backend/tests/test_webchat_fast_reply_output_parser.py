from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_fast_output_parser_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.services.webchat_fast_output_parser import (  # noqa: E402
    FastReplyParseError,
    UnexpectedToolCallError,
    parse_openclaw_fast_reply,
)


def _valid_text(**overrides):
    payload = {
        "reply": "Hi, this is Speedy. How can I help you today?",
        "intent": "greeting",
        "tracking_number": None,
        "handoff_required": False,
        "handoff_reason": None,
        "recommended_agent_action": None,
    }
    payload.update(overrides)
    import json

    return json.dumps(payload)


def test_accepts_pure_json_output_text():
    parsed = parse_openclaw_fast_reply({"output_text": _valid_text()})

    assert parsed.reply.startswith("Hi")
    assert parsed.intent == "greeting"
    assert parsed.handoff_required is False


def test_rejects_markdown_fenced_json():
    with pytest.raises(FastReplyParseError):
        parse_openclaw_fast_reply({"output_text": "```json\n" + _valid_text() + "\n```"})


def test_rejects_text_before_json():
    with pytest.raises(FastReplyParseError):
        parse_openclaw_fast_reply({"output_text": "Sure. " + _valid_text()})


def test_rejects_text_after_json():
    with pytest.raises(FastReplyParseError):
        parse_openclaw_fast_reply({"output_text": _valid_text() + "\nThanks"})


def test_rejects_function_call_output():
    with pytest.raises(UnexpectedToolCallError):
        parse_openclaw_fast_reply({"output": [{"type": "function_call", "name": "send_message"}]})


def test_rejects_missing_required_keys():
    with pytest.raises(FastReplyParseError):
        parse_openclaw_fast_reply({"output_text": '{"reply":"hello"}'})


def test_rejects_internal_terms_in_reply():
    with pytest.raises(FastReplyParseError):
        parse_openclaw_fast_reply({"output_text": _valid_text(reply="OpenClaw gateway says hello")})
