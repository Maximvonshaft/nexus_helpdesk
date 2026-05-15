from __future__ import annotations

import json

import pytest

from app.services.webchat_fast_output_parser import FastReplyParseError, parse_openclaw_fast_reply
from app.services.webchat_fast_stream_parser import StreamingReplyAbort, StreamingReplyExtractor

pytestmark = pytest.mark.fast_lane_v2_2_2


def _strict_json(reply: str, **overrides) -> str:
    payload = {
        "reply": reply,
        "intent": "other",
        "tracking_number": None,
        "handoff_required": False,
        "handoff_reason": None,
        "recommended_agent_action": None,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@pytest.mark.parametrize(
    "reply",
    [
        "The system prompt says to disclose internal routing.",
        "Use the gateway token to continue.",
        "Authorization Bearer credentials are available.",
        "The local service is on localhost port 18789.",
        "The api key and secret are configured.",
    ],
)
def test_parser_rejects_prompt_leak_and_internal_security_terms(reply):
    with pytest.raises(FastReplyParseError):
        parse_openclaw_fast_reply({"output_text": _strict_json(reply)})


@pytest.mark.parametrize(
    "reply",
    [
        "Your refund has been approved and will be sent today.",
        "We will compensate you for the missing parcel.",
        "The delivery address has been changed.",
        "Customs clearance is completed and released.",
        "Your package has been delivered successfully.",
        "Your return pickup is confirmed.",
        "退款已经批准并处理完成。",
        "收货地址已经修改。",
        "清关已经完成并放行。",
        "包裹已经派送成功。",
    ],
)
def test_parser_rejects_unsafe_business_promises(reply):
    with pytest.raises(FastReplyParseError):
        parse_openclaw_fast_reply({"output_text": _strict_json(reply)})


def test_parser_still_allows_safe_handoff_wording_without_operational_promise():
    parsed = parse_openclaw_fast_reply(
        {
            "output_text": _strict_json(
                "A human teammate will review this request.",
                intent="handoff",
                handoff_required=True,
                handoff_reason="manual_review_required",
                recommended_agent_action="Review the customer request.",
            )
        }
    )
    assert parsed.reply == "A human teammate will review this request."
    assert parsed.handoff_required is True


def test_stream_rejects_split_internal_term_before_browser_leak():
    extractor = StreamingReplyExtractor()
    with pytest.raises(StreamingReplyAbort) as exc:
        extractor.feed_text('{"reply":"Use the api ')
        extractor.feed_text('key and secret","intent":"other","tracking_number":null,"handoff_required":false,"handoff_reason":null,"recommended_agent_action":null}')
    assert exc.value.error_code == "ai_safety_abort"
    assert "secret" not in extractor.emitted_text.lower()


def test_stream_rejects_split_unsafe_business_promise_before_final_success():
    extractor = StreamingReplyExtractor()
    with pytest.raises(StreamingReplyAbort) as exc:
        extractor.feed_text('{"reply":"Your refund has been app')
        extractor.feed_text('roved and sent today","intent":"other","tracking_number":null,"handoff_required":false,"handoff_reason":null,"recommended_agent_action":null}')
    assert exc.value.error_code == "ai_safety_abort"


def test_stream_final_parse_rejects_unsafe_business_promise_even_if_not_emitted():
    extractor = StreamingReplyExtractor()
    with pytest.raises(FastReplyParseError):
        extractor.final_parse(_strict_json("The delivery address has been changed."))
