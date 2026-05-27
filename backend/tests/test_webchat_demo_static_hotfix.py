from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (BACKEND_ROOT / path).read_text(encoding="utf-8")


def test_embed_widget_uses_direct_json_before_stream_code():
    text = _read("app/static/webchat/widget.js")

    assert "NEXUSDESK_DIRECT_JSON_FAST_REPLY_BEGIN" in text
    assert "return api('/api/webchat/fast-reply'" in text
    assert "return fallbackToNonStream();" in text
    assert "streamApi('/api/webchat/fast-reply/stream'" in text
    assert text.index("return fallbackToNonStream();") < text.index("streamApi('/api/webchat/fast-reply/stream'")


def test_demo_quick_actions_are_intent_mapped_and_handoff_aware():
    text = _read("app/static/webchat/demo/js/app.js")

    assert "NEXUSDESK_DEMO_QUICK_ACTION_INTENT_MAPPING" in text
    assert "const QUICK_ACTION_MESSAGES = Object.freeze" in text
    assert "track: 'Please help me track my parcel. I will provide the tracking number.'" in text
    assert "redelivery: 'I need help with redelivery.'" in text
    assert "human: 'Talk to human'" in text
    assert "if (action === 'track') clearContext();" in text
    assert "function setQuickButtonsDisabled(disabled)" in text
    assert "if (data && data.handoff_required)" in text
