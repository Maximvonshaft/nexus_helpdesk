from __future__ import annotations

import pytest

from app.services.webchat_fast_output_parser import FastReplyParseError
from app.services.webchat_fast_stream_parser import StreamingReplyAbort, StreamingReplyExtractor
from app.services.webchat_openclaw_stream_adapter import ContentDelta, ToolCallDetected

pytestmark = pytest.mark.fast_lane_v2_2_2


def _feed(chunks: list[str]) -> str:
    extractor = StreamingReplyExtractor()
    out = []
    for chunk in chunks:
        delta = extractor.feed_text(chunk)
        if delta:
            out.append(delta.text)
    tail = extractor.flush()
    if tail:
        out.append(tail.text)
    return "".join(out)


def test_split_reply_key_outputs_reply_only():
    text = _feed(['{"re', 'ply":"Hello","intent":"greeting","tracking_number":null,"handoff_required":false,"handoff_reason":null,"recommended_agent_action":null}'])
    assert text == "Hello"
    assert "intent" not in text
    assert '{"reply"' not in text


def test_escaped_quote_output():
    text = _feed(['{"reply":"Please type \\"tracking number\\" only","intent":"tracking_missing_number","tracking_number":null,"handoff_required":false,"handoff_reason":null,"recommended_agent_action":null}'])
    assert text == 'Please type "tracking number" only'


def test_unicode_escape_output():
    text = _feed(['{"reply":"Hello \\u4f60\\u597d","intent":"greeting","tracking_number":null,"handoff_required":false,"handoff_reason":null,"recommended_agent_action":null}'])
    assert text == "Hello 你好"


def test_split_internal_term_openclaw_aborts_without_leak():
    extractor = StreamingReplyExtractor()
    with pytest.raises(StreamingReplyAbort) as exc:
        extractor.feed_text('{"reply":"Open')
        extractor.feed_text('Claw internal","intent":"other","tracking_number":null,"handoff_required":false,"handoff_reason":null,"recommended_agent_action":null}')
    assert exc.value.error_code == "ai_safety_abort"
    assert "OpenClaw" not in extractor.emitted_text


def test_markdown_fenced_json_rejected():
    extractor = StreamingReplyExtractor()
    with pytest.raises(StreamingReplyAbort):
        extractor.feed_text('```json\n{"reply":"Hello"}\n```')


def test_empty_reply_rejected_on_final_parse():
    extractor = StreamingReplyExtractor()
    with pytest.raises(FastReplyParseError):
        extractor.final_parse('{"reply":"","intent":"greeting","tracking_number":null,"handoff_required":false,"handoff_reason":null,"recommended_agent_action":null}')


def test_tool_call_rejected():
    extractor = StreamingReplyExtractor()
    with pytest.raises(StreamingReplyAbort) as exc:
        extractor.feed_event(ToolCallDetected("response.tool_call.delta"))
    assert exc.value.error_code == "ai_unexpected_tool_call"


def test_reply_then_invalid_final_rejected():
    extractor = StreamingReplyExtractor()
    long_reply = "Hello, this is a deliberately long customer-visible reply so holdback emits at least one delta."
    delta = extractor.feed_event(ContentDelta('{"reply":' + repr(long_reply).replace("'", '"') + ',"intent":"greeting","tracking_number":null,"handoff_required":"bad","handoff_reason":null,"recommended_agent_action":null}'))
    assert delta is not None
    with pytest.raises(FastReplyParseError):
        extractor.final_parse('{"reply":"Hello","intent":"greeting","tracking_number":null,"handoff_required":"bad","handoff_reason":null,"recommended_agent_action":null}')
