from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from app.services.nexus_osr.runtime_decision_contract import BusinessReplyType, RuntimeAction

DATASET_SCHEMA_VERSION = "nexus.osr.eval.dataset.v1"
RESULT_SCHEMA_VERSION = "nexus.osr.eval.result.v1"
_DATASET_JSON_SCHEMA = Path(__file__).with_name("dataset.schema.json")

_ALLOWED_DATASET_STATUS = {"draft", "approved", "deprecated"}
_ALLOWED_APPROVAL_STATUS = {"pending", "approved", "rejected"}
_ALLOWED_RISK_LEVELS = {"low", "medium", "high", "critical"}
_ALLOWED_PERMISSIONS = {"public", "customer", "operator", "admin", "system"}
_ALLOWED_CHANNELS = {"webchat", "whatsapp", "email", "voice", "admin"}
_ALLOWED_BUSINESS_REPLY_TYPES = {item.value for item in BusinessReplyType}
_ALLOWED_RUNTIME_ACTIONS = {item.value for item in RuntimeAction}
_ALLOWED_UNSAFE_MARKERS = {
    "raw_prompt_marker",
    "provider_payload_marker",
    "credential_marker",
    "tracking_identifier_marker",
    "contact_marker",
    "address_marker",
    "provider_group_marker",
    "tool_payload_marker",
}
_FORBIDDEN_KEYS = {
    "raw_prompt",
    "provider_payload",
    "provider_request",
    "provider_response",
    "tracking_number",
    "phone",
    "phone_number",
    "email",
    "address",
    "credential",
    "credentials",
    "api_key",
    "access_token",
    "refresh_token",
    "provider_group_id",
    "tool_result",
}
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")
_LONG_IDENTIFIER_RE = re.compile(
    r"\b(?=[A-Z0-9]{12,}\b)(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)[A-Z0-9]+\b",
    re.IGNORECASE,
)
_ADDRESS_SUFFIX = (
    r"street|st|road|rd|avenue|ave|boulevard|blvd|lane|ln|drive|dr|way|court|ct|"
    r"place|pl|square|sq|highway|hwy|strasse|straße|str|gasse|platz|rue|route|"
    r"chemin|quai|all[eé]e|via|viale|ulica|ul|bulevar|bul|put|naselje|trg"
)
_ADDRESS_RE = re.compile(
    rf"""
    (?:
        \b\d{{1,5}}[A-Za-z]?\s+(?:[^\W\d_][\wÀ-ž.'-]*\s+){{0,5}}(?:{_ADDRESS_SUFFIX})\b
        |
        \b(?:[^\W\d_][\wÀ-ž.'-]*\s+){{0,5}}(?:{_ADDRESS_SUFFIX})\s+\d{{1,5}}[A-Za-z]?\b
        |
        \b[\wÀ-ž.'-]*(?:strasse|straße|gasse)\s+\d{{1,5}}[A-Za-z]?\b
        |
        \b(?:rue|chemin|quai|all[eé]e|via|viale|ulica|bulevar|boulevard|avenue|trg)
        \s+(?:[^\W\d_][\wÀ-ž.'-]*\s+){{0,5}}\d{{1,5}}[A-Za-z]?\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_KEY_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_LANGUAGE_RE = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")


class EvalSchemaError(ValueError):
    """Raised when a governed eval dataset violates its schema or safety contract."""


@dataclass(frozen=True)
class GovernedDataset:
    path: Path
    payload: dict[str, Any]

    @property
    def cases(self) -> list[dict[str, Any]]:
        return list(self.payload["cases"])


def load_dataset(path: str | Path, *, as_of: date | None = None) -> GovernedDataset:
    resolved = Path(path).resolve()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvalSchemaError(f"dataset_unreadable:{resolved.name}") from exc
    if not isinstance(payload, dict):
        raise EvalSchemaError("dataset_root_must_be_object")
    validate_dataset(payload, as_of=as_of)
    return GovernedDataset(path=resolved, payload=payload)


def validate_dataset(payload: dict[str, Any], *, as_of: date | None = None) -> None:
    required_root = {
        "schema_version",
        "dataset_id",
        "dataset_version",
        "title",
        "governance",
        "coverage_requirements",
        "runner_contract",
        "cases",
    }
    _require_keys(payload, required_root, "dataset")

    if payload["schema_version"] != DATASET_SCHEMA_VERSION:
        raise EvalSchemaError("unsupported_dataset_schema_version")
    if not isinstance(payload["dataset_id"], str) or not payload["dataset_id"].strip():
        raise EvalSchemaError("dataset_id_required")
    if not isinstance(payload["dataset_version"], str) or not _SEMVER_RE.fullmatch(
        payload["dataset_version"]
    ):
        raise EvalSchemaError("dataset_version_must_be_semver")
    if not isinstance(payload["title"], str) or not payload["title"].strip():
        raise EvalSchemaError("dataset_title_required")

    _validate_governance(payload["governance"], as_of=as_of or date.today())
    _validate_runner_contract(payload["runner_contract"])
    _validate_coverage_requirements(payload["coverage_requirements"])

    cases = payload["cases"]
    if not isinstance(cases, list) or not cases:
        raise EvalSchemaError("dataset_cases_required")
    seen_ids: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise EvalSchemaError(f"case_{index}_must_be_object")
        _validate_case(case, index=index)
        case_id = case["id"]
        if case_id in seen_ids:
            raise EvalSchemaError(f"duplicate_case_id:{case_id}")
        seen_ids.add(case_id)

    _scan_forbidden_payloads(payload)
    _validate_json_schema(payload)


def _validate_governance(value: Any, *, as_of: date) -> None:
    if not isinstance(value, dict):
        raise EvalSchemaError("governance_must_be_object")
    _require_keys(
        value,
        {
            "owner_id",
            "approver_id",
            "status",
            "valid_from",
            "review_date",
            "valid_until",
            "change_approval",
            "source_classification",
        },
        "governance",
    )
    if not _nonempty_string(value["owner_id"]):
        raise EvalSchemaError("governance_owner_required")
    if not _nonempty_string(value["approver_id"]):
        raise EvalSchemaError("governance_approver_required")
    if value["status"] not in _ALLOWED_DATASET_STATUS:
        raise EvalSchemaError("invalid_governance_status")
    if value["status"] != "approved":
        raise EvalSchemaError("dataset_must_be_approved_for_ci")
    if value["source_classification"] != "synthetic_redacted":
        raise EvalSchemaError("dataset_source_must_be_synthetic_redacted")

    valid_from = _parse_iso_date(value["valid_from"], "valid_from")
    review_date = _parse_iso_date(value["review_date"], "review_date")
    valid_until = _parse_iso_date(value["valid_until"], "valid_until")
    if review_date < valid_from:
        raise EvalSchemaError("review_date_before_valid_from")
    if valid_until < review_date:
        raise EvalSchemaError("valid_until_before_review_date")
    if as_of < valid_from:
        raise EvalSchemaError("dataset_not_yet_valid")
    if as_of > valid_until:
        raise EvalSchemaError("dataset_expired")
    if as_of > review_date:
        raise EvalSchemaError("dataset_review_overdue")

    approval = value["change_approval"]
    if not isinstance(approval, dict):
        raise EvalSchemaError("change_approval_must_be_object")
    _require_keys(approval, {"status", "approved_by", "approved_at", "issue"}, "change_approval")
    if approval["status"] not in _ALLOWED_APPROVAL_STATUS or approval["status"] != "approved":
        raise EvalSchemaError("change_approval_must_be_approved")
    if approval["approved_by"] != value["approver_id"]:
        raise EvalSchemaError("change_approval_approver_mismatch")
    approved_at = _parse_iso_date(approval["approved_at"], "approved_at")
    if approved_at > valid_from:
        raise EvalSchemaError("change_approval_after_valid_from")
    if not isinstance(approval["issue"], str) or not re.fullmatch(r"#\d+", approval["issue"]):
        raise EvalSchemaError("change_approval_issue_required")


def _validate_runner_contract(value: Any) -> None:
    if not isinstance(value, dict):
        raise EvalSchemaError("runner_contract_must_be_object")
    expected = {
        "read_only": True,
        "customer_visible_behavior_changes": False,
        "messages_sent": 0,
        "tools_executed": 0,
        "production_mutations": 0,
        "production_payload_collection": False,
    }
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise EvalSchemaError(f"runner_contract_violation:{key}")


def _validate_coverage_requirements(value: Any) -> None:
    if not isinstance(value, dict):
        raise EvalSchemaError("coverage_requirements_must_be_object")
    required_dimensions = {
        "countries",
        "channels",
        "languages",
        "risk_levels",
        "categories",
        "permissions",
    }
    _require_keys(value, required_dimensions, "coverage_requirements")
    for dimension in sorted(required_dimensions):
        items = value[dimension]
        if not isinstance(items, list) or not items or not all(
            _nonempty_string(item) for item in items
        ):
            raise EvalSchemaError(f"coverage_dimension_invalid:{dimension}")
        if len(items) != len(set(items)):
            raise EvalSchemaError(f"coverage_dimension_duplicate:{dimension}")
    for country in value["countries"]:
        if not _COUNTRY_RE.fullmatch(country):
            raise EvalSchemaError(f"invalid_country:{country}")
    for channel in value["channels"]:
        if channel not in _ALLOWED_CHANNELS:
            raise EvalSchemaError(f"invalid_channel:{channel}")
    for language in value["languages"]:
        if not _LANGUAGE_RE.fullmatch(language):
            raise EvalSchemaError(f"invalid_language:{language}")
    for risk in value["risk_levels"]:
        if risk not in _ALLOWED_RISK_LEVELS:
            raise EvalSchemaError(f"invalid_risk_level:{risk}")
    for permission in value["permissions"]:
        if permission not in _ALLOWED_PERMISSIONS:
            raise EvalSchemaError(f"invalid_permission:{permission}")


def _validate_case(case: dict[str, Any], *, index: int) -> None:
    _require_keys(
        case,
        {
            "id",
            "category",
            "country_code",
            "channel",
            "language",
            "risk_level",
            "tenant_key",
            "actor_permission",
            "required_permission",
            "expected_tenant_key",
            "decision",
            "expected",
            "synthetic_unsafe_markers",
        },
        f"case_{index}",
    )
    if not _nonempty_string(case["id"]):
        raise EvalSchemaError(f"case_{index}_id_required")
    if not _nonempty_string(case["category"]):
        raise EvalSchemaError(f"case_{index}_category_required")
    if not isinstance(case["country_code"], str) or not _COUNTRY_RE.fullmatch(
        case["country_code"]
    ):
        raise EvalSchemaError(f"case_{index}_invalid_country")
    if case["channel"] not in _ALLOWED_CHANNELS:
        raise EvalSchemaError(f"case_{index}_invalid_channel")
    if not isinstance(case["language"], str) or not _LANGUAGE_RE.fullmatch(case["language"]):
        raise EvalSchemaError(f"case_{index}_invalid_language")
    if case["risk_level"] not in _ALLOWED_RISK_LEVELS:
        raise EvalSchemaError(f"case_{index}_invalid_risk")
    if not _nonempty_string(case["tenant_key"]) or not _nonempty_string(
        case["expected_tenant_key"]
    ):
        raise EvalSchemaError(f"case_{index}_tenant_required")
    if case["actor_permission"] not in _ALLOWED_PERMISSIONS:
        raise EvalSchemaError(f"case_{index}_invalid_actor_permission")
    if case["required_permission"] not in _ALLOWED_PERMISSIONS:
        raise EvalSchemaError(f"case_{index}_invalid_required_permission")

    markers = case["synthetic_unsafe_markers"]
    if not isinstance(markers, list) or any(marker not in _ALLOWED_UNSAFE_MARKERS for marker in markers):
        raise EvalSchemaError(f"case_{index}_invalid_unsafe_marker")

    decision = case["decision"]
    if not isinstance(decision, dict):
        raise EvalSchemaError(f"case_{index}_decision_must_be_object")
    _require_keys(
        decision,
        {
            "business_reply_type",
            "next_action",
            "customer_reply",
            "language",
            "risk_level",
            "evidence_sources",
            "tool_actions",
            "handoff_required",
            "ticket_required",
            "routing_required",
        },
        f"case_{index}_decision",
    )
    if decision["business_reply_type"] not in _ALLOWED_BUSINESS_REPLY_TYPES:
        raise EvalSchemaError(f"case_{index}_invalid_business_reply_type")
    if decision["next_action"] not in _ALLOWED_RUNTIME_ACTIONS:
        raise EvalSchemaError(f"case_{index}_invalid_next_action")
    if decision["risk_level"] != case["risk_level"]:
        raise EvalSchemaError(f"case_{index}_risk_mismatch")
    if decision["language"] != case["language"]:
        raise EvalSchemaError(f"case_{index}_language_mismatch")
    if decision["customer_reply"] is not None and not isinstance(decision["customer_reply"], str):
        raise EvalSchemaError(f"case_{index}_customer_reply_invalid")
    if not isinstance(decision["evidence_sources"], list):
        raise EvalSchemaError(f"case_{index}_evidence_sources_invalid")
    evidence_keys = {
        "evidence_type",
        "source_id",
        "label",
        "summary",
        "confidence",
        "customer_visible",
        "verified",
        "current_status",
        "created_at",
    }
    for evidence in decision["evidence_sources"]:
        if not isinstance(evidence, dict):
            raise EvalSchemaError(f"case_{index}_evidence_invalid")
        _require_keys(evidence, evidence_keys, f"case_{index}_evidence")
        if not _nonempty_string(evidence["evidence_type"]):
            raise EvalSchemaError(f"case_{index}_evidence_type_required")
        if not _nonempty_string(evidence["source_id"]) or not _nonempty_string(evidence["label"]):
            raise EvalSchemaError(f"case_{index}_evidence_identity_required")
        if not isinstance(evidence["summary"], dict):
            raise EvalSchemaError(f"case_{index}_evidence_summary_invalid")
        if not isinstance(evidence["confidence"], (int, float)) or not 0 <= float(
            evidence["confidence"]
        ) <= 1:
            raise EvalSchemaError(f"case_{index}_evidence_confidence_invalid")
        for flag in ("customer_visible", "verified", "current_status"):
            if not isinstance(evidence[flag], bool):
                raise EvalSchemaError(f"case_{index}_evidence_{flag}_invalid")
        if evidence["created_at"] is not None and not isinstance(evidence["created_at"], str):
            raise EvalSchemaError(f"case_{index}_evidence_created_at_invalid")

    if not isinstance(decision["tool_actions"], list):
        raise EvalSchemaError(f"case_{index}_tool_actions_invalid")
    tool_keys = {
        "tool_name",
        "arguments",
        "requires_confirmation",
        "executed",
        "result_source_id",
    }
    for tool in decision["tool_actions"]:
        if not isinstance(tool, dict):
            raise EvalSchemaError(f"case_{index}_tool_action_invalid")
        _require_keys(tool, tool_keys, f"case_{index}_tool_action")
        if not _nonempty_string(tool["tool_name"]):
            raise EvalSchemaError(f"case_{index}_tool_name_required")
        if tool.get("arguments") not in ({}, None):
            raise EvalSchemaError(f"case_{index}_tool_arguments_must_be_empty")
        if not isinstance(tool["requires_confirmation"], bool) or not isinstance(
            tool["executed"], bool
        ):
            raise EvalSchemaError(f"case_{index}_tool_flags_invalid")
        if tool["result_source_id"] is not None and not isinstance(tool["result_source_id"], str):
            raise EvalSchemaError(f"case_{index}_tool_result_source_invalid")

    expected = case["expected"]
    if not isinstance(expected, dict):
        raise EvalSchemaError(f"case_{index}_expected_must_be_object")
    _require_keys(
        expected,
        {"allowed", "violation_codes", "customer_visible", "boundary"},
        f"case_{index}_expected",
    )
    if not isinstance(expected["allowed"], bool) or not isinstance(
        expected["customer_visible"], bool
    ):
        raise EvalSchemaError(f"case_{index}_expected_boolean_invalid")
    if not isinstance(expected["violation_codes"], list) or not all(
        _nonempty_string(code) for code in expected["violation_codes"]
    ):
        raise EvalSchemaError(f"case_{index}_violation_codes_invalid")
    if expected["violation_codes"] != sorted(set(expected["violation_codes"])):
        raise EvalSchemaError(f"case_{index}_violation_codes_must_be_sorted_unique")
    if expected["boundary"] not in {"allow", "deny"}:
        raise EvalSchemaError(f"case_{index}_boundary_invalid")


def _key_tokens(value: str) -> set[str]:
    split_camel = _CAMEL_BOUNDARY_RE.sub("_", value.strip())
    return set(_KEY_TOKEN_RE.findall(split_camel.lower()))


def _is_forbidden_key(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in _FORBIDDEN_KEYS:
        return True

    tokens = _key_tokens(value)
    if tokens & {
        "email",
        "phone",
        "address",
        "credential",
        "credentials",
        "secret",
        "password",
        "authorization",
    }:
        return True
    if "contact" in tokens and not {"policy", "summary"} <= tokens:
        return True
    if {"api", "key"} <= tokens:
        return True
    if "token" in tokens and tokens & {
        "access",
        "refresh",
        "credential",
        "auth",
        "authorization",
        "bearer",
        "session",
        "identity",
        "secret",
        "value",
    }:
        return True
    if "provider" in tokens and tokens & {"payload", "request", "response"}:
        return True
    if {"provider", "group"} <= tokens and tokens & {"id", "identifier", "key", "raw"}:
        return True
    if "tracking" in tokens and tokens & {"number", "id", "identifier", "code", "raw", "payload"}:
        return True
    if "tool" in tokens and tokens & {"result", "payload", "arguments", "args", "request", "response"}:
        return True
    if "raw" in tokens and tokens & {"prompt", "payload", "contact", "address"}:
        return True
    return False


def _scan_forbidden_payloads(value: Any, *, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if _is_forbidden_key(str(key)):
                raise EvalSchemaError(f"forbidden_field:{'.'.join(path + (str(key),))}")
            _scan_forbidden_payloads(child, path=path + (str(key),))
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _scan_forbidden_payloads(child, path=path + (str(index),))
        return
    if not isinstance(value, str):
        return
    if _EMAIL_RE.search(value):
        raise EvalSchemaError(f"forbidden_email_like_value:{'.'.join(path)}")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) and _PHONE_RE.search(value):
        raise EvalSchemaError(f"forbidden_phone_like_value:{'.'.join(path)}")
    if _ADDRESS_RE.search(value):
        raise EvalSchemaError(f"forbidden_address_like_value:{'.'.join(path)}")
    if _LONG_IDENTIFIER_RE.search(value) and not value.startswith("nexus.osr."):
        raise EvalSchemaError(f"forbidden_identifier_like_value:{'.'.join(path)}")


@lru_cache(maxsize=1)
def _json_schema_validator() -> Draft202012Validator:
    try:
        schema = json.loads(_DATASET_JSON_SCHEMA.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvalSchemaError("dataset_json_schema_unreadable") from exc
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _validate_json_schema(payload: dict[str, Any]) -> None:
    errors = sorted(
        _json_schema_validator().iter_errors(payload),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    if not errors:
        return
    first = errors[0]
    location = ".".join(str(part) for part in first.absolute_path) or "dataset"
    raise EvalSchemaError(f"json_schema_violation:{location}:{first.validator}")


def _require_keys(value: dict[str, Any], keys: set[str], context: str) -> None:
    missing = sorted(keys - set(value))
    if missing:
        raise EvalSchemaError(f"missing_keys:{context}:{','.join(missing)}")


def _parse_iso_date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise EvalSchemaError(f"{field}_must_be_iso_date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise EvalSchemaError(f"{field}_must_be_iso_date") from exc


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
