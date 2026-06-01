from app.services.webchat_fast_policy import (
    SUPPORT_HOURS_REPLY,
    is_support_hours_question,
    match_support_hours_policy_reply,
)


def test_support_hours_question_detection_english():
    assert is_support_hours_question("What are your customer service hours for parcel delivery support?")
    assert is_support_hours_question("When is support available?")
    assert is_support_hours_question("Are you available 24/7 for parcel support?")


def test_support_hours_question_detection_chinese_and_german():
    assert is_support_hours_question("客服时间是什么时候？")
    assert is_support_hours_question("Was sind die Supportzeiten?")


def test_non_hours_question_not_matched():
    assert not is_support_hours_question("Track parcel ABC123456789")
    assert not is_support_hours_question("I want to speak to a human agent")


def test_support_hours_policy_reply_contract():
    data = match_support_hours_policy_reply(
        "What are your customer service hours for parcel delivery support?"
    )

    assert data is not None
    assert data["ok"] is True
    assert data["ai_generated"] is False
    assert data["reply_source"] == "server_support_hours_policy"
    assert data["reply"] == SUPPORT_HOURS_REPLY
    assert "24/7" in data["reply"]
    assert "Monday-Friday" in data["reply"]
    assert "09:00-17:00" in data["reply"]
    assert data["handoff_required"] is False
    assert data["ticket_creation_queued"] is False
    assert data["error_code"] is None
    assert data["evidence_trace"]["retrieval"] == "server_policy"
    assert data["evidence_trace"]["source"] == "support_hours_policy"
    assert data["evidence_trace"]["policy_evidence_present"] is True
    assert data["evidence_trace"]["raw_tracking_number_exposed"] is False
