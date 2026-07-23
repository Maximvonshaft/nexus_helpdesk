from __future__ import annotations

import pytest

from app.services.customer_language import (
    detect_customer_language,
    resolve_conversation_language,
)


@pytest.mark.parametrize(
    "text",
    [
        "I need a human to review this",
        "I want to speak with a support agent",
        "Please help me with my delivery",
    ],
)
def test_english_support_requests_are_not_misclassified_by_single_letter_articles(
    text: str,
):
    decision = detect_customer_language(text)

    assert decision.language == "en"
    assert decision.source == "latin_marker"


@pytest.mark.parametrize(
    "text",
    [
        "Preciso falar com um agente humano",
        "Olá, preciso de ajuda com meu pacote",
        "Quero o rastreamento da minha entrega",
    ],
)
def test_portuguese_requires_language_specific_evidence(text: str):
    decision = detect_customer_language(text)

    assert decision.language == "pt"
    assert decision.source == "latin_marker"


def test_conversation_history_keeps_a_reliable_portuguese_decision():
    decision = resolve_conversation_language(
        "ABCD12345678",
        previous_customer_messages=["Preciso de ajuda com meu pacote"],
    )

    assert decision.language == "pt"
    assert decision.source == "conversation_history:latin_marker"
