from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pytest

from app.services.nexus_osr.runtime_decision_contract import BusinessReplyType, RuntimeAction
from evals.nexus_osr.schema import EvalSchemaError, load_dataset, validate_dataset

DATASET = Path(__file__).resolve().parents[1] / "evals" / "nexus_osr" / "datasets" / "m7-governed-eval-v1.json"
JSON_SCHEMA = Path(__file__).resolve().parents[1] / "evals" / "nexus_osr" / "dataset.schema.json"


def _payload() -> dict:
    return copy.deepcopy(load_dataset(DATASET).payload)


def _first_summary(payload: dict) -> dict:
    return next(
        evidence["summary"]
        for case in payload["cases"]
        for evidence in case["decision"]["evidence_sources"]
    )


def test_governed_dataset_schema_accepts_approved_synthetic_dataset() -> None:
    dataset = load_dataset(DATASET)

    assert dataset.payload["schema_version"] == "nexus.osr.eval.dataset.v1"
    assert dataset.payload["governance"]["status"] == "approved"
    assert dataset.payload["governance"]["source_classification"] == "synthetic_redacted"
    assert len(dataset.cases) >= 15


def test_dataset_rejects_unapproved_review_lifecycle() -> None:
    payload = _payload()
    payload["governance"]["status"] = "draft"

    with pytest.raises(EvalSchemaError, match="dataset_must_be_approved_for_ci"):
        validate_dataset(payload)


def test_dataset_rejects_forbidden_raw_payload_fields() -> None:
    payload = _payload()
    payload["cases"][0]["decision"]["raw_prompt"] = "synthetic"

    with pytest.raises(EvalSchemaError, match="forbidden_field"):
        validate_dataset(payload)


@pytest.mark.parametrize(
    "field_name",
    [
        "shipping_address",
        "provider_payload_json",
        "providerResponseBody",
        "customer_email_value",
        "tracking_number_raw",
        "trackingIdentifier",
        "credential_blob",
        "api_key_hash",
        "accessTokenValue",
        "provider_group_id_raw",
        "tool_result_body",
        "toolArgumentsJson",
        "customer_contact",
        "contactDetails",
        "contact-value",
        "recipient_contact_record",
        "contact_policy_summary_payload",
        "customer_contact_policy_summary",
        "client_secret",
        "clientSecretValue",
        "password_value",
        "authorization-header",
        "x_authorization_header",
        "bearerToken",
        "session_token",
        "session-token-value",
        "identity_token",
        "token_value",
    ],
)
def test_dataset_rejects_derived_sensitive_field_names(field_name: str) -> None:
    payload = _payload()
    _first_summary(payload)[field_name] = "synthetic-redacted"

    with pytest.raises(EvalSchemaError, match="forbidden_field"):
        validate_dataset(payload)


def test_dataset_rejects_nested_derived_sensitive_field_names() -> None:
    payload = _payload()
    _first_summary(payload)["safe_wrapper"] = {
        "nested": {
            "customerContactValue": "synthetic-redacted",
        }
    }

    with pytest.raises(EvalSchemaError, match="forbidden_field"):
        validate_dataset(payload)


def test_forbidden_field_failure_does_not_echo_sensitive_value() -> None:
    payload = _payload()
    sensitive_value = "synthetic-private-credential-material"
    _first_summary(payload)["client_secret"] = sensitive_value

    with pytest.raises(EvalSchemaError, match="forbidden_field") as exc_info:
        validate_dataset(payload)

    assert sensitive_value not in str(exc_info.value)


@pytest.mark.parametrize(
    "address",
    [
        "221B Baker Street",
        "10 Downing Street",
        "Bahnhofstrasse 12",
        "Rue de Lausanne 15",
        "Bulevar Svetog Petra Cetinjskog 45",
    ],
)
def test_dataset_rejects_common_street_address_values(address: str) -> None:
    payload = _payload()
    _first_summary(payload)["location_hint"] = address

    with pytest.raises(EvalSchemaError, match="forbidden_address_like_value"):
        validate_dataset(payload)


def test_dataset_allows_safe_operational_summary_fields() -> None:
    payload = _payload()
    _first_summary(payload).update(
        {
            "delivery_status_summary": "Delivery route is ready",
            "tracking_policy_summary": "Customer-visible format guidance",
            "provider_readiness_state": "unavailable",
            "tool_action_count": 0,
            "review_date_label": "2026-07-10",
            "token_usage_count": 17,
            "token_budget_summary": "within synthetic limit",
            "contact_policy_summary": "Use the governed escalation path",
            "contactless_delivery_summary": "enabled",
        }
    )

    validate_dataset(payload)


def test_dataset_rejects_contact_like_values() -> None:
    payload = _payload()
    payload["cases"][0]["decision"]["customer_reply"] = "contact synthetic-user@example.test"

    with pytest.raises(EvalSchemaError, match="forbidden_email_like_value"):
        validate_dataset(payload)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("business_reply_type", "knowledge_answr", "invalid_business_reply_type"),
        ("business_reply_type", "unknown_reply", "invalid_business_reply_type"),
        ("next_action", "blok", "invalid_next_action"),
        ("next_action", "unknown_action", "invalid_next_action"),
    ],
)
def test_dataset_rejects_unknown_or_misspelled_decision_values(
    field: str,
    value: str,
    error: str,
) -> None:
    payload = _payload()
    payload["cases"][0]["decision"][field] = value

    with pytest.raises(EvalSchemaError, match=error):
        validate_dataset(payload)


def test_semantic_and_json_schema_decision_vocabularies_match_runtime_enums() -> None:
    schema = json.loads(JSON_SCHEMA.read_text(encoding="utf-8"))
    decision = schema["$defs"]["decision"]["properties"]

    assert set(decision["business_reply_type"]["enum"]) == {
        item.value for item in BusinessReplyType
    }
    assert set(decision["next_action"]["enum"]) == {item.value for item in RuntimeAction}

    payload = _payload()
    for reply_type in BusinessReplyType:
        candidate = copy.deepcopy(payload)
        candidate["cases"][0]["decision"]["business_reply_type"] = reply_type.value
        validate_dataset(candidate)
    for action in RuntimeAction:
        candidate = copy.deepcopy(payload)
        candidate["cases"][0]["decision"]["next_action"] = action.value
        validate_dataset(candidate)


def test_dataset_rejects_nonempty_tool_arguments() -> None:
    payload = _payload()
    tool_case = next(case for case in payload["cases"] if case["category"] == "governed_tool")
    tool_case["decision"]["tool_actions"][0]["arguments"] = {"value": "synthetic"}

    with pytest.raises(EvalSchemaError, match="tool_arguments_must_be_empty"):
        validate_dataset(payload)


def test_dataset_review_date_and_validity_are_enforced() -> None:
    payload = _payload()

    with pytest.raises(EvalSchemaError, match="dataset_review_overdue"):
        validate_dataset(payload, as_of=date(2026, 8, 11))

    with pytest.raises(EvalSchemaError, match="dataset_expired"):
        validate_dataset(payload, as_of=date(2026, 10, 11))


def test_json_schema_rejects_unknown_case_properties() -> None:
    payload = _payload()
    payload["cases"][0]["unknown_contract_field"] = True

    with pytest.raises(EvalSchemaError, match="json_schema_violation"):
        validate_dataset(payload)
