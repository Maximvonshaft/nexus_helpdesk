from __future__ import annotations

from app.services.customer_language import detect_customer_language, resolve_conversation_language


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


def test_conversation_language_inherits_latest_reliable_customer_language() -> None:
    decision = resolve_conversation_language(
        "I already provided it above.",
        previous_customer_messages=("Hello, please help with my parcel.", "CH01026681375"),
    )

    assert decision.language == "en"
    assert decision.source.startswith("conversation_history:")


def test_conversation_language_allows_explicit_language_switch() -> None:
    decision = resolve_conversation_language(
        "请继续用中文回答",
        previous_customer_messages=("Hello, please help with my parcel.",),
    )

    assert decision.language == "zh"
    assert decision.source == "script"
