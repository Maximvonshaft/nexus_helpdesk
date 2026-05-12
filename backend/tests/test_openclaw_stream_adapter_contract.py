from __future__ import annotations

import pytest

from app.services.webchat_openclaw_stream_adapter import (
    Completed,
    ContentDelta,
    OpenClawResponsesStreamAdapter,
    StreamError,
    ToolCallDetected,
)

pytestmark = pytest.mark.fast_lane_v2_2_2


def test_sse_output_text_delta():
    events = OpenClawResponsesStreamAdapter().feed_sse_block(
        'event: response.output_text.delta\ndata: {"delta":"Hi"}'
    )
    assert events == [ContentDelta('Hi')]


def test_jsonl_output_text_delta():
    events = OpenClawResponsesStreamAdapter().feed_json_line('{"type":"response.output_text.delta","delta":"Hello"}')
    assert events == [ContentDelta('Hello')]


def test_response_created_ignored():
    assert OpenClawResponsesStreamAdapter().feed_json_line('{"type":"response.created"}') == []


def test_response_completed_output_text():
    events = OpenClawResponsesStreamAdapter().feed_json_line('{\"type\":\"response.completed\",\"output_text\":\"{\\\"reply\\\":\\\"OK\\\"}\"}')
    assert isinstance(events[0], Completed)
    assert events[0].full_text == '{"reply":"OK"}'


def test_response_error_to_stream_error():
    events = OpenClawResponsesStreamAdapter().feed_json_line('{"type":"response.error","error":{"message":"bad"}}')
    assert events == [StreamError('openclaw_stream_error', 'bad')]


def test_tool_function_call_detected():
    adapter = OpenClawResponsesStreamAdapter()
    assert adapter.feed_json_line('{"type":"response.tool_call.delta","name":"x"}') == [ToolCallDetected('response.tool_call.delta')]
    assert adapter.feed_json_line('{"type":"response.function_call.delta","name":"x"}') == [ToolCallDetected('response.function_call.delta')]


def test_done_is_not_content_delta():
    assert OpenClawResponsesStreamAdapter().feed_sse_block('data: [DONE]') == []
    assert OpenClawResponsesStreamAdapter().feed_json_line('[DONE]') == []


def test_multiline_sse_data_joined_with_newline():
    block = 'event: response.completed\ndata: {"type":"response.completed",\ndata: "output_text":"x"}'
    events = OpenClawResponsesStreamAdapter().feed_sse_block(block)
    assert isinstance(events[0], Completed)
    assert events[0].full_text == 'x'


def test_malformed_json_to_stream_error():
    events = OpenClawResponsesStreamAdapter().feed_json_line('{bad json')
    assert isinstance(events[0], StreamError)
    assert events[0].error_code == 'openclaw_malformed_json'
