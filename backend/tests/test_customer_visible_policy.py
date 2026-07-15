import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.customer_visible_policy import evaluate_customer_visible_policy  # noqa: E402


def test_empty_body_blocks() -> None:
    decision = evaluate_customer_visible_policy("")
    assert decision.level == "block"
    assert decision.allowed is False


def test_assigned_secret_blocks() -> None:
    decision = evaluate_customer_visible_policy("api_key=1234567890abcdef")
    assert decision.allowed is False
    assert decision.reasons == ["assigned_secret_disclosure"]


def test_private_key_blocks() -> None:
    decision = evaluate_customer_visible_policy("-----BEGIN PRIVATE KEY-----")
    assert decision.allowed is False


def test_internal_reasoning_blocks() -> None:
    decision = evaluate_customer_visible_policy("<think>internal reasoning</think>")
    assert decision.allowed is False


def test_business_topics_are_not_interpreted_by_content_policy() -> None:
    messages = (
        "My parcel is lost. What information do you need and what happens next?",
        "The customer asked about a refund, customs clearance, and compensation.",
        "我的包裹丢了，请告诉我需要什么信息，后续会怎么处理。",
        "Your parcel has been delivered.",
    )
    for message in messages:
        decision = evaluate_customer_visible_policy(message)
        assert decision.allowed is True
        assert decision.level == "allow"


def test_policy_does_not_mutate_customer_visible_body() -> None:
    body = " Complete Runtime reply. "
    decision = evaluate_customer_visible_policy(body)
    assert decision.allowed is True
    assert decision.normalized_body == body
