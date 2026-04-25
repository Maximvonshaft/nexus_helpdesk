import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.outbound_safety import evaluate_outbound_safety  # noqa: E402


class DummyTicket:
    id = 1


def test_empty_body_blocks():
    decision = evaluate_outbound_safety(DummyTicket(), '', 'manual')
    assert decision.level == 'block'
    assert decision.allowed is False


def test_secret_like_content_blocks():
    decision = evaluate_outbound_safety(DummyTicket(), 'SECRET_KEY leaked in stack trace token password', 'manual')
    assert decision.level == 'block'
    assert decision.allowed is False


def test_logistics_claim_without_evidence_requires_review():
    decision = evaluate_outbound_safety(DummyTicket(), 'Your parcel will arrive today.', 'manual', has_fact_evidence=False)
    assert decision.level == 'review'
    assert decision.requires_human_review is True


def test_ai_auto_reply_defaults_to_review():
    decision = evaluate_outbound_safety(DummyTicket(), 'We have checked your parcel.', 'ai_auto_reply', has_fact_evidence=False)
    assert decision.level == 'review'
    assert decision.requires_human_review is True


def test_safe_manual_reply_allows():
    decision = evaluate_outbound_safety(DummyTicket(), 'We have received your request and will check it shortly.', 'manual')
    assert decision.level == 'allow'
    assert decision.allowed is True
