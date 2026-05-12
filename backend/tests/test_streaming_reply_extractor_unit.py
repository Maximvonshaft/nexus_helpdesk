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


def _strict_json(reply: str = 'Hello', **overrides) -> str:
    import json
    payload = {
        'reply': reply,
        'intent': 'greeting',
        'tracking_number': None,
        'handoff_required': False,
        'handoff_reason': None,
        'recommended_agent_action': None,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def test_split_reply_key_outputs_reply_only():
    text = _feed(['{"re', 'ply":"Hello","intent":"greeting","tracking_number":null,"handoff_required":false,"handoff_reason":null,"recommended_agent_action":null}'])
    assert text == 'Hello'
    assert 'intent' not in text
    assert '{"reply"' not in text


def test_reply_value_split_outputs_correct_reply_only():
    text = _feed(['{"reply":"He', 'llo","intent":"greeting","tracking_number":null,"handoff_required":false,"handoff_reason":null,"recommended_agent_action":null}'])
    assert text == 'Hello'


def test_escaped_quote_output():
    text = _feed(['{\"reply\":\"Please type \\\"tracking number\\\" only\",\"intent\":\"tracking_missing_number\",\"tracking_number\":null,\"handoff_required\":false,\"handoff_reason\":null,\"recommended_agent_action\":null}'])
    assert text == 'Please type "tracking number" only'


def test_unicode_escape_output():
    text = _feed(['{"reply":"Hello \u4f60\u597d","intent":"greeting","tracking_number":null,"handoff_required":false,"handoff_reason":null,"recommended_agent_action":null}'])
    assert text == 'Hello 你好'


def test_split_internal_term_open_plus_claw_aborts_without_leak():
    extractor = StreamingReplyExtractor()
    with pytest.raises(StreamingReplyAbort) as exc:
        extractor.feed_text('{"reply":"Open')
        extractor.feed_text('Claw internal","intent":"other","tracking_number":null,"handoff_required":false,"handoff_reason":null,"recommended_agent_action":null}')
    assert exc.value.error_code == 'ai_safety_abort'
    assert 'OpenClaw' not in extractor.emitted_text


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
        extractor.feed_event(ToolCallDetected('response.tool_call.delta'))
    assert exc.value.error_code == 'ai_unexpected_tool_call'


def test_reply_then_invalid_final_rejected():
    extractor = StreamingReplyExtractor()
    long_reply = 'Hello, this is a deliberately long customer-visible reply so holdback emits at least one delta.'
    delta = extractor.feed_event(ContentDelta('{"reply":' + repr(long_reply).replace("'", '"') + ',"intent":"greeting","tracking_number":null,"handoff_required":"bad","handoff_reason":null,"recommended_agent_action":null}'))
    assert delta is not None
    with pytest.raises(FastReplyParseError):
        extractor.final_parse('{"reply":"Hello","intent":"greeting","tracking_number":null,"handoff_required":"bad","handoff_reason":null,"recommended_agent_action":null}')


def test_valid_strict_json_final_parse_passes():
    extractor = StreamingReplyExtractor()
    parsed = extractor.final_parse(_strict_json('Hello'))
    assert parsed.reply == 'Hello'
    assert parsed.handoff_required is False


def test_direct_strict_dict_final_parse_passes():
    extractor = StreamingReplyExtractor()
    parsed = extractor.final_parse({
        'reply': 'Hello',
        'intent': 'greeting',
        'tracking_number': None,
        'handoff_required': False,
        'handoff_reason': None,
        'recommended_agent_action': None,
    })
    assert parsed.reply == 'Hello'


def test_openclaw_envelope_final_parse_passes():
    extractor = StreamingReplyExtractor()
    parsed = extractor.final_parse({
        'response': {
            'output_text': _strict_json('Hello')
        }
    })
    assert parsed.reply == 'Hello'
    assert parsed.intent == 'greeting'


def test_final_parse_uses_buffered_strict_json_when_completed_has_no_text():
    extractor = StreamingReplyExtractor()
    extractor.feed_text(_strict_json('Hello from buffer'))
    parsed = extractor.final_parse(None)
    assert parsed.reply == 'Hello from buffer'
    assert parsed.handoff_required is False
