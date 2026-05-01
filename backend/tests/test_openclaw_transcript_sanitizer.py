import pytest
from app.services.openclaw_bridge import sanitize_openclaw_transcript_payload

def test_assistant_thinking_type():
    msg = {
        "role": "assistant",
        "content": [
            {"type": "thinking", "text": "I need to look up info."},
            {"type": "text", "text": "Hello world!"}
        ]
    }
    res = sanitize_openclaw_transcript_payload(msg)
    assert res['body_text'] == "Hello world!"
    assert res['redacted'] is True
    # Thinking block should be removed
    assert len(res['content_json']['content']) == 1
    assert res['content_json']['content'][0]['text'] == "Hello world!"
    
def test_assistant_pure_think_tags():
    msg = {
        "role": "assistant",
        "text": "<think>Thinking about stuff</think>"
    }
    res = sanitize_openclaw_transcript_payload(msg)
    assert res['body_text'] == "[redacted by NexusDesk transcript sync safety gate]"
    assert res['content_json']['redacted'] is True
    assert res['content_json']['reason'] == "NexusDesk transcript sync safety gate"
    assert res['redacted'] is True

def test_assistant_think_and_final_tags():
    msg = {
        "role": "assistant",
        "text": "<think>I think...</think><final>The answer is 42.</final>"
    }
    res = sanitize_openclaw_transcript_payload(msg)
    assert res['body_text'] == "The answer is 42."
    assert res['content_json']['text'] == "The answer is 42."
    assert res['redacted'] is True
    
def test_assistant_tool_metadata():
    msg = {
        "role": "assistant",
        "toolCalls": [{"id": "xyz", "name": "do_it"}],
        "thoughtSignature": "abc"
    }
    res = sanitize_openclaw_transcript_payload(msg)
    assert res['body_text'] == "[redacted by NexusDesk transcript sync safety gate]"
    assert "toolCalls" not in res['content_json']
    assert res['content_json']['redacted'] is True
    assert res['redacted'] is True

def test_user_message_keeps_text_strips_metadata():
    msg = {
        "role": "user",
        "text": "Hello toolName",
        "toolCalls": []
    }
    res = sanitize_openclaw_transcript_payload(msg)
    # the word toolName is a keyword but it's a user message
    assert res['body_text'] == "Hello toolName"
    assert "toolCalls" not in res['content_json']
    assert res['redacted'] is True
    assert res['content_json']['text'] == "Hello toolName"

def test_no_risk_message():
    msg = {
        "role": "assistant",
        "text": "Regular message without any tags."
    }
    res = sanitize_openclaw_transcript_payload(msg)
    assert res['body_text'] == "Regular message without any tags."
    assert res['redacted'] is False
    assert res['content_json']['text'] == "Regular message without any tags."

