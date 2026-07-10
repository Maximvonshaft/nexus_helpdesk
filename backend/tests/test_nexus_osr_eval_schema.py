from __future__ import annotations

import copy
from datetime import date
from pathlib import Path

import pytest

from evals.nexus_osr.schema import EvalSchemaError, load_dataset, validate_dataset

DATASET = Path(__file__).resolve().parents[1] / "evals" / "nexus_osr" / "datasets" / "m7-governed-eval-v1.json"


def test_governed_dataset_schema_accepts_approved_synthetic_dataset() -> None:
    dataset = load_dataset(DATASET)

    assert dataset.payload["schema_version"] == "nexus.osr.eval.dataset.v1"
    assert dataset.payload["governance"]["status"] == "approved"
    assert dataset.payload["governance"]["source_classification"] == "synthetic_redacted"
    assert len(dataset.cases) >= 15


def test_dataset_rejects_unapproved_review_lifecycle() -> None:
    payload = copy.deepcopy(load_dataset(DATASET).payload)
    payload["governance"]["status"] = "draft"

    with pytest.raises(EvalSchemaError, match="dataset_must_be_approved_for_ci"):
        validate_dataset(payload)


def test_dataset_rejects_forbidden_raw_payload_fields() -> None:
    payload = copy.deepcopy(load_dataset(DATASET).payload)
    payload["cases"][0]["decision"]["raw_prompt"] = "synthetic"

    with pytest.raises(EvalSchemaError, match="forbidden_field"):
        validate_dataset(payload)


def test_dataset_rejects_contact_like_values() -> None:
    payload = copy.deepcopy(load_dataset(DATASET).payload)
    payload["cases"][0]["decision"]["customer_reply"] = "contact synthetic-user@example.test"

    with pytest.raises(EvalSchemaError, match="forbidden_email_like_value"):
        validate_dataset(payload)


def test_dataset_rejects_nonempty_tool_arguments() -> None:
    payload = copy.deepcopy(load_dataset(DATASET).payload)
    tool_case = next(case for case in payload["cases"] if case["category"] == "governed_tool")
    tool_case["decision"]["tool_actions"][0]["arguments"] = {"value": "synthetic"}

    with pytest.raises(EvalSchemaError, match="tool_arguments_must_be_empty"):
        validate_dataset(payload)


def test_dataset_review_date_and_validity_are_enforced() -> None:
    payload = copy.deepcopy(load_dataset(DATASET).payload)

    with pytest.raises(EvalSchemaError, match="dataset_review_overdue"):
        validate_dataset(payload, as_of=date(2026, 8, 11))

    with pytest.raises(EvalSchemaError, match="dataset_expired"):
        validate_dataset(payload, as_of=date(2026, 10, 11))


def test_json_schema_rejects_unknown_case_properties() -> None:
    payload = copy.deepcopy(load_dataset(DATASET).payload)
    payload["cases"][0]["unknown_contract_field"] = True

    with pytest.raises(EvalSchemaError, match="json_schema_violation"):
        validate_dataset(payload)
