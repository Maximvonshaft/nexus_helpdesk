from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

MAX_BYTES = 32 * 1024 * 1024
MAX_ITEMS = 2000
MAX_EXCEPTION_DAYS = 180
_SAFE_OWNER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@/-]{1,79}$")


class PolicyInputError(ValueError):
    pass


def _load(path: Path) -> Any:
    if not path.is_file():
        raise PolicyInputError(f"missing_input:{path.name}")
    if path.stat().st_size > MAX_BYTES:
        raise PolicyInputError(f"input_too_large:{path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyInputError(f"invalid_json:{path.name}") from exc


def _require_accountable_owner(value: object) -> str:
    owner = str(value or "").strip()
    if not _SAFE_OWNER.fullmatch(owner):
        raise PolicyInputError("exception_owner_invalid")
    if owner.lower() in {"unknown", "unassigned", "none", "n/a"}:
        raise PolicyInputError("exception_owner_unaccountable")
    return owner


def _require_reason(value: object) -> str:
    reason = " ".join(str(value or "").strip().split())
    if not 12 <= len(reason) <= 240:
        raise PolicyInputError("exception_reason_invalid")
    lowered = reason.lower()
    if any(token in lowered for token in ("bearer ", "password", "secret=", "token=")):
        raise PolicyInputError("exception_reason_sensitive")
    return reason


def _require_expiry(value: object, *, today: date) -> str:
    try:
        expiry = date.fromisoformat(str(value or ""))
    except ValueError as exc:
        raise PolicyInputError("exception_expiry_invalid") from exc
    if expiry <= today:
        raise PolicyInputError("exception_expired")
    if expiry > today + timedelta(days=MAX_EXCEPTION_DAYS):
        raise PolicyInputError("exception_expiry_too_long")
    return expiry.isoformat()


def _validate_trivy(payload: Any) -> int:
    if not isinstance(payload, dict):
        raise PolicyInputError("trivy_report_not_object")
    if "Results" not in payload or not isinstance(payload["Results"], list):
        raise PolicyInputError("trivy_results_missing")
    if len(payload["Results"]) > MAX_ITEMS:
        raise PolicyInputError("trivy_results_excessive")
    return len(payload["Results"])


def _validate_sbom(payload: Any) -> int:
    if not isinstance(payload, dict) or payload.get("bomFormat") != "CycloneDX":
        raise PolicyInputError("sbom_schema_invalid")
    if "components" not in payload or not isinstance(payload["components"], list):
        raise PolicyInputError("sbom_components_missing")
    if not payload["components"]:
        raise PolicyInputError("sbom_components_empty")
    if len(payload["components"]) > MAX_ITEMS:
        raise PolicyInputError("sbom_components_excessive")
    return len(payload["components"])


def _validate_vulnerability_exceptions(payload: Any, *, today: date) -> int:
    if not isinstance(payload, dict) or payload.get("schema_version") != "nexus_container_vulnerability_exceptions_v1":
        raise PolicyInputError("vulnerability_exception_schema_invalid")
    entries = payload.get("entries")
    if not isinstance(entries, list) or len(entries) > MAX_ITEMS:
        raise PolicyInputError("vulnerability_exception_entries_invalid")
    expected = {
        "vulnerability_id",
        "package",
        "installed_version",
        "reason",
        "expires_on",
        "owner",
    }
    seen: set[tuple[str, str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != expected:
            raise PolicyInputError("vulnerability_exception_shape_invalid")
        key = (
            str(entry["vulnerability_id"] or "").strip(),
            str(entry["package"] or "").strip(),
            str(entry["installed_version"] or "").strip(),
        )
        if not all(key) or key in seen:
            raise PolicyInputError("vulnerability_exception_key_invalid")
        seen.add(key)
        _require_accountable_owner(entry["owner"])
        _require_reason(entry["reason"])
        _require_expiry(entry["expires_on"], today=today)
    return len(entries)


def _validate_license_policy(
    payload: Any, *, today: date
) -> dict[tuple[str, str, str], tuple[str, str]]:
    if not isinstance(payload, dict) or payload.get("schema_version") != "nexus_container_license_policy_v1":
        raise PolicyInputError("license_policy_schema_invalid")
    dispositions: dict[str, set[str]] = {}
    for field in ("allowed", "denied", "review"):
        values = payload.get(field)
        if not isinstance(values, list) or len(values) > MAX_ITEMS:
            raise PolicyInputError(f"license_policy_{field}_invalid")
        normalized = {str(value or "").strip() for value in values}
        if "" in normalized or len(normalized) != len(values):
            raise PolicyInputError(f"license_policy_{field}_invalid")
        dispositions[field] = normalized
    if any(
        dispositions[left] & dispositions[right]
        for left, right in (("allowed", "denied"), ("allowed", "review"), ("denied", "review"))
    ):
        raise PolicyInputError("license_policy_dispositions_overlap")
    if payload.get("unknown_action") not in {"allow", "review", "deny"}:
        raise PolicyInputError("license_policy_unknown_action_invalid")

    exceptions = payload.get("exceptions")
    if not isinstance(exceptions, list) or len(exceptions) > MAX_ITEMS:
        raise PolicyInputError("license_exception_entries_invalid")
    expected = {"package", "version", "license", "owner", "expires_on", "reason"}
    records: dict[tuple[str, str, str], tuple[str, str]] = {}
    for entry in exceptions:
        if not isinstance(entry, dict) or set(entry) != expected:
            raise PolicyInputError("license_exception_shape_invalid")
        key = (
            str(entry["package"] or "").strip(),
            str(entry["version"] or "").strip(),
            str(entry["license"] or "").strip(),
        )
        if not all(key) or key in records:
            raise PolicyInputError("license_exception_key_invalid")
        if key[2] in dispositions["denied"]:
            raise PolicyInputError("license_exception_for_denied_license")
        if key[2] not in dispositions["review"]:
            raise PolicyInputError("license_exception_not_review_disposition")
        owner = _require_accountable_owner(entry["owner"])
        _require_reason(entry["reason"])
        expiry = _require_expiry(entry["expires_on"], today=today)
        records[key] = (owner, expiry)
    return records


def _validate_license_compliance(
    payload: Any,
    *,
    today: date,
    policy_exceptions: dict[tuple[str, str, str], tuple[str, str]],
) -> int:
    if not isinstance(payload, dict) or payload.get("schema_version") != "nexus_container_license_compliance_v1":
        raise PolicyInputError("license_compliance_schema_invalid")
    entries = payload.get("entries")
    if not isinstance(entries, list) or len(entries) > MAX_ITEMS:
        raise PolicyInputError("license_compliance_entries_invalid")
    required = {
        "package",
        "version",
        "purl",
        "license",
        "owner",
        "expires_on",
        "source",
        "notice_path",
        "modified",
        "replacement_supported",
        "obligations",
    }
    records: dict[tuple[str, str, str], tuple[str, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not required.issubset(entry):
            raise PolicyInputError("license_compliance_shape_invalid")
        key = (
            str(entry["package"] or "").strip(),
            str(entry["version"] or "").strip(),
            str(entry["license"] or "").strip(),
        )
        if not all(key) or key in records:
            raise PolicyInputError("license_compliance_key_invalid")
        owner = _require_accountable_owner(entry["owner"])
        expiry = _require_expiry(entry["expires_on"], today=today)
        source = str(entry["source"] or "").strip()
        if not source.startswith("https://") or len(source) > 240:
            raise PolicyInputError("license_compliance_source_invalid")
        notice_path = str(entry["notice_path"] or "").strip()
        if not notice_path or notice_path.startswith("/") or ".." in notice_path.split("/"):
            raise PolicyInputError("license_compliance_notice_path_invalid")
        if entry["modified"] is not False or entry["replacement_supported"] is not True:
            raise PolicyInputError("license_compliance_component_state_invalid")
        obligations = entry["obligations"]
        if not isinstance(obligations, list) or not obligations or len(obligations) > 32:
            raise PolicyInputError("license_compliance_obligations_invalid")
        records[key] = (owner, expiry)

    missing = sorted(set(policy_exceptions) - set(records))
    if missing:
        raise PolicyInputError("license_exception_compliance_missing")
    unused = sorted(set(records) - set(policy_exceptions))
    if unused:
        raise PolicyInputError("license_compliance_record_unused")
    for key, policy_authority in policy_exceptions.items():
        if records[key] != policy_authority:
            raise PolicyInputError("license_compliance_authority_mismatch")
    return len(records)


def validate(
    *,
    trivy_path: Path,
    sbom_path: Path,
    vulnerability_exceptions_path: Path,
    license_policy_path: Path,
    license_compliance_path: Path,
    output_path: Path,
    today: date,
) -> int:
    result_count = _validate_trivy(_load(trivy_path))
    component_count = _validate_sbom(_load(sbom_path))
    vulnerability_exception_count = _validate_vulnerability_exceptions(
        _load(vulnerability_exceptions_path), today=today
    )
    policy_exceptions = _validate_license_policy(_load(license_policy_path), today=today)
    compliance_count = _validate_license_compliance(
        _load(license_compliance_path),
        today=today,
        policy_exceptions=policy_exceptions,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "schema_version": "nexus_release_image_policy_input_validation_v1",
                "status": "pass",
                "evaluated_on": today.isoformat(),
                "trivy_result_count": result_count,
                "sbom_component_count": component_count,
                "vulnerability_exception_count": vulnerability_exception_count,
                "license_exception_count": len(policy_exceptions),
                "license_compliance_record_count": compliance_count,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trivy", type=Path, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--vulnerability-exceptions", type=Path, required=True)
    parser.add_argument("--license-policy", type=Path, required=True)
    parser.add_argument("--license-compliance", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--today", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    try:
        return validate(
            trivy_path=args.trivy,
            sbom_path=args.sbom,
            vulnerability_exceptions_path=args.vulnerability_exceptions,
            license_policy_path=args.license_policy,
            license_compliance_path=args.license_compliance,
            output_path=args.output,
            today=args.today,
        )
    except PolicyInputError as exc:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "schema_version": "nexus_release_image_policy_input_validation_v1",
                    "status": "fail",
                    "evaluated_on": args.today.isoformat(),
                    "reason": str(exc)[:120],
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"release_image_policy_input_error:{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
