from __future__ import annotations

from pathlib import Path

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


def test_response_output_text_done_becomes_completed():
    line = '{"type":"response.output_text.done","text":"{\\"reply\\":\\"OK\\"}"}'
    events = OpenClawResponsesStreamAdapter().feed_json_line(line)
    assert events == [Completed(full_payload={"type":"response.output_text.done","text":"{\"reply\":\"OK\"}"}, full_text='{"reply":"OK"}')]


def test_response_completed_extracts_nested_output_text():
    payload = '{"type":"response.completed","response":{"output":[{"type":"message","content":[{"type":"output_text","text":"{\\"reply\\":\\"OK\\"}"}]}]}}'
    events = OpenClawResponsesStreamAdapter().feed_json_line(payload)
    assert events[0].full_text == '{"reply":"OK"}'


def test_response_error_to_stream_error():
    events = OpenClawResponsesStreamAdapter().feed_json_line('{"type":"response.error","error":{"message":"bad"}}')
    assert events == [StreamError('openclaw_stream_error', 'bad')]


def test_tool_function_call_detected():
    adapter = OpenClawResponsesStreamAdapter()
    assert adapter.feed_json_line('{"type":"response.tool_call.delta","name":"x"}') == [ToolCallDetected('response.tool_call.delta')]
    assert adapter.feed_json_line('{"type":"response.function_call.delta","name":"x"}') == [ToolCallDetected('response.function_call.delta')]
    assert adapter.feed_json_line('{"type":"response.output_item.added","item":{"type":"function_call","name":"x"}}') == [ToolCallDetected('response.output_item.added')]


def test_message_output_item_added_is_ignored_not_tool_call():
    adapter = OpenClawResponsesStreamAdapter()
    assert adapter.feed_json_line('{"type":"response.output_item.added","item":{"type":"message","role":"assistant","content":[{"type":"output_text","text":""}]}}') == []


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


def test_real_openclaw_sse_fixture_yields_deltas_and_completed_without_tool_call():
    fixture = Path(__file__).with_name('fixtures') / 'openclaw' / 'responses_sse_real.txt'
    text = fixture.read_text(encoding='utf-8')
    events = OpenClawResponsesStreamAdapter().feed_raw_chunks([text])
    assert any(isinstance(event, ContentDelta) for event in events)
    assert any(isinstance(event, Completed) for event in events)
    assert not any(isinstance(event, ToolCallDetected) for event in events)
    assert not any(isinstance(event, StreamError) for event in events)
