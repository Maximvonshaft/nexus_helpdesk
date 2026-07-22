from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services import governance_service


def test_role_template_key_and_capabilities_are_fail_closed() -> None:
    assert governance_service.clean_role_key("  Regional.Support-Lead ") == "regional.support-lead"
    with pytest.raises(HTTPException) as invalid_key:
        governance_service.clean_role_key("../admin")
    assert invalid_key.value.status_code == 400

    capabilities = governance_service.clean_capabilities(["ticket.read", "ticket.read"])
    assert capabilities == ["ticket.read"]
    with pytest.raises(HTTPException) as unknown:
        governance_service.clean_capabilities(["ticket.read", "runtime.yolo"])
    assert unknown.value.status_code == 400


def test_market_reference_values_are_normalized_and_validated() -> None:
    assert governance_service.validate_languages(["EN", "pt-BR", "en"]) == ["en", "pt-br"]
    assert governance_service.validate_currency(" eur ") == "EUR"
    assert governance_service.validate_currency(None) is None
    assert governance_service.validate_timezone("Europe/Podgorica") == "Europe/Podgorica"

    with pytest.raises(HTTPException):
        governance_service.validate_languages(["not a language"])
    with pytest.raises(HTTPException):
        governance_service.validate_currency("EURO")
    with pytest.raises(HTTPException):
        governance_service.validate_timezone("Mars/Olympus")


def test_risk_and_base_role_contracts_are_closed() -> None:
    assert governance_service.validate_risk_level("Sensitive") == "sensitive"
    assert governance_service.validate_base_role("agent").value == "agent"
    with pytest.raises(HTTPException):
        governance_service.validate_risk_level("unbounded")
    with pytest.raises(HTTPException):
        governance_service.validate_base_role("owner")
