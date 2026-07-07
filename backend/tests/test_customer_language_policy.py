from __future__ import annotations

from app.services.customer_language import detect_customer_language


def test_customer_language_detects_latest_german_message() -> None:
    decision = detect_customer_language("kannst du mal schauen, welche Zustand die Sendung ist?")

    assert decision.language == "de"
    assert decision.source == "latin_marker"


def test_customer_language_detects_english_greeting() -> None:
    decision = detect_customer_language("hello")

    assert decision.language == "en"


def test_customer_language_ignores_reference_only_message() -> None:
    decision = detect_customer_language("CH020000129026", explicit="en")

    assert decision.language is None
    assert decision.source == "empty_or_reference_only"
