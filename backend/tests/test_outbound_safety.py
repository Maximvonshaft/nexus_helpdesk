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


def test_logistics_claim_with_operator_evidence_allows_manual_reply():
    decision = evaluate_outbound_safety(
        DummyTicket(),
        'Your parcel will arrive today.',
        'queued',
        fact_evidence={
            'evidence_source': 'tracking_api',
            'tracking_number': 'TRK123',
            'event_code': 'OUT_FOR_DELIVERY',
            'checked_by': 7,
            'evidence_summary': 'Latest tracking event says out for delivery.',
        },
    )
    assert decision.level == 'allow'
    assert decision.allowed is True


def test_high_risk_logistics_claim_still_requires_review_even_with_evidence():
    decision = evaluate_outbound_safety(
        DummyTicket(),
        'Your parcel lost parcel compensation has been approved.',
        'queued',
        fact_evidence={'tracking_number': 'TRK123', 'evidence_summary': 'Operator checked case notes.'},
    )
    assert decision.level == 'review'
    assert decision.requires_human_review is True


def test_ai_auto_reply_defaults_to_review():
    decision = evaluate_outbound_safety(DummyTicket(), 'We have checked your parcel.', 'ai_auto_reply', has_fact_evidence=False)
    assert decision.level == 'review'
    assert decision.requires_human_review is True


def test_ai_auto_reply_with_evidence_still_requires_review():
    decision = evaluate_outbound_safety(
        DummyTicket(),
        'Your parcel will arrive today.',
        'ai_auto_reply',
        fact_evidence={'tracking_number': 'TRK123', 'evidence_summary': 'Tracking event verified.'},
    )
    assert decision.level == 'review'
    assert decision.requires_human_review is True


def test_safe_manual_reply_allows():
    decision = evaluate_outbound_safety(DummyTicket(), 'We have received your request and will check it shortly.', 'manual')
    assert decision.level == 'allow'
    assert decision.allowed is True
