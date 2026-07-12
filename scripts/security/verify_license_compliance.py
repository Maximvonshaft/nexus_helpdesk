from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

MAX_BYTES = 2 * 1024 * 1024
MAX_DAYS = 180
_REQUIRED_OBLIGATIONS = {
    "retain_license_text",
    "retain_copyright_notice",
    "provide_upstream_source_reference",
    "allow_component_replacement",
}
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:/@-]{0,199}$")
_SAFE_PYTHON_PURL = re.compile(
    r"^pkg:(?:pypi/[A-Za-z0-9._-]+|generic/python)@[A-Za-z0-9._+!-]{1,100}$"
)
_SAFE_NPM_PURL = re.compile(
    r"^pkg:npm/(?:%40[A-Za-z0-9._-]+/)?[A-Za-z0-9._-]+@[A-Za-z0-9._+!~-]{1,120}(?:\?[A-Za-z0-9._~%=&+-]{1,300})?$"
)
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class ComplianceError(ValueError):
    pass


def _load(path: Path) -> Any:
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ComplianceError(f"input_invalid:{path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ComplianceError(f"json_invalid:{path.name}") from exc


def _expiry(value: object, *, today: date) -> date:
    try:
        parsed = date.fromisoformat(str(value or ""))
    except ValueError as exc:
        raise ComplianceError("compliance_expiry_invalid") from exc
    if parsed <= today or parsed > today + timedelta(days=MAX_DAYS):
        raise ComplianceError("compliance_expiry_out_of_range")
    return parsed


def _safe(value: object, *, label: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_LABEL.fullmatch(text):
        raise ComplianceError(f"{label}_invalid")
    return text


def _safe_purl(value: object) -> str:
    text = str(value or "").strip()
    if not (
        _SAFE_PYTHON_PURL.fullmatch(text) or _SAFE_NPM_PURL.fullmatch(text)
    ):
        raise ComplianceError("sbom_purl_invalid")
    return text


def _sbom_components(sbom: dict[str, Any]) -> dict[str, dict[str, Any]]:
    components = sbom.get("components")
    if not isinstance(components, list):
        raise ComplianceError("sbom_components_invalid")
    result: dict[str, dict[str, Any]] = {}
    for component in components:
        if not isinstance(component, dict):
            raise ComplianceError("sbom_component_invalid")
        purl = _safe_purl(component.get("purl"))
        if purl in result:
            raise ComplianceError("sbom_purl_duplicate")
        result[purl] = component
    return result


def _component_license_values(component: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for entry in component.get("licenses") or []:
        if not isinstance(entry, dict):
            continue
        if isinstance(entry.get("expression"), str):
            values.update(
                token
                for token in re.findall(
                    r"[A-Za-z0-9][A-Za-z0-9.+-]*", entry["expression"]
                )
                if token not in {"AND", "OR", "WITH"}
            )
        info = entry.get("license")
        if isinstance(info, dict) and isinstance(info.get("id"), str):
            values.add(info["id"])
    return values


def _installed_components(installed: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    components = installed.get("components")
    if not isinstance(components, list):
        raise ComplianceError("installed_components_invalid")
    expected = {"purl", "package", "version", "license_files"}
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in components:
        if not isinstance(item, dict) or set(item) != expected:
            raise ComplianceError("installed_component_keys_invalid")
        key = (
            _safe_purl(item.get("purl")),
            _safe(item.get("package"), label="installed_package"),
            _safe(item.get("version"), label="installed_version"),
        )
        if key in result:
            raise ComplianceError("installed_component_duplicate")
        result[key] = item
    return result


def verify(
    *,
    compliance_path: Path,
    policy_path: Path,
    sbom_path: Path,
    installed_path: Path,
    notice_path: Path,
    output_path: Path,
    today: date,
) -> int:
    compliance = _load(compliance_path)
    policy = _load(policy_path)
    sbom = _load(sbom_path)
    installed = _load(installed_path)
    if (
        not isinstance(compliance, dict)
        or compliance.get("schema_version")
        != "nexus_container_license_compliance_v1"
    ):
        raise ComplianceError("compliance_schema_invalid")
    if (
        not isinstance(policy, dict)
        or policy.get("schema_version") != "nexus_container_license_policy_v1"
    ):
        raise ComplianceError("policy_schema_invalid")
    if not isinstance(sbom, dict) or sbom.get("bomFormat") != "CycloneDX":
        raise ComplianceError("sbom_schema_invalid")
    if (
        not isinstance(installed, dict)
        or installed.get("schema_version") != "nexus_installed_license_evidence_v1"
    ):
        raise ComplianceError("installed_evidence_schema_invalid")

    entries = compliance.get("entries")
    if not isinstance(entries, list) or not entries or len(entries) > 50:
        raise ComplianceError("compliance_entries_invalid")
    exceptions = policy.get("exceptions")
    if not isinstance(exceptions, list):
        raise ComplianceError("policy_exceptions_invalid")
    expected_exception_keys = {
        "purl",
        "package",
        "version",
        "license",
        "owner",
        "expires_on",
        "reason",
    }
    exception_map: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in exceptions:
        if not isinstance(item, dict) or set(item) != expected_exception_keys:
            raise ComplianceError("policy_exception_keys_invalid")
        key = (
            _safe_purl(item.get("purl")),
            _safe(item.get("package"), label="policy_package"),
            _safe(item.get("version"), label="policy_version"),
            _safe(item.get("license"), label="policy_license"),
        )
        if key in exception_map:
            raise ComplianceError("policy_exception_duplicate")
        exception_map[key] = item

    sbom_by_purl = _sbom_components(sbom)
    installed_map = _installed_components(installed)
    notice = notice_path.read_text(encoding="utf-8")
    checked: list[dict[str, object]] = []
    seen: set[tuple[str, str, str, str]] = set()

    for raw in entries:
        if not isinstance(raw, dict):
            raise ComplianceError("compliance_entry_invalid")
        expected_keys = {
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
        if set(raw) != expected_keys:
            raise ComplianceError("compliance_entry_keys_invalid")
        package = _safe(raw["package"], label="package")
        version = _safe(raw["version"], label="version")
        purl = _safe_purl(raw["purl"])
        license_id = _safe(raw["license"], label="license")
        owner = _safe(raw["owner"], label="owner")
        expires = _expiry(raw["expires_on"], today=today)
        source = str(raw["source"] or "").strip()
        if not source.startswith("https://") or len(source) > 500:
            raise ComplianceError("compliance_source_invalid")
        if raw["notice_path"] != notice_path.name:
            raise ComplianceError("compliance_notice_path_invalid")
        if raw["modified"] is not False or raw["replacement_supported"] is not True:
            raise ComplianceError("compliance_distribution_contract_invalid")
        obligations = raw["obligations"]
        if not isinstance(obligations, list) or set(obligations) != _REQUIRED_OBLIGATIONS:
            raise ComplianceError("compliance_obligations_invalid")
        key = (purl, package, version, license_id)
        if key in seen:
            raise ComplianceError("compliance_entry_duplicate")
        seen.add(key)

        component = sbom_by_purl.get(purl)
        if (
            component is None
            or component.get("name") != package
            or component.get("version") != version
        ):
            raise ComplianceError("compliance_sbom_identity_mismatch")
        if license_id not in _component_license_values(component):
            raise ComplianceError("compliance_sbom_license_mismatch")

        installed_component = installed_map.get((purl, package, version))
        if not isinstance(installed_component, dict):
            raise ComplianceError("compliance_installed_component_missing")
        files = installed_component.get("license_files")
        if not isinstance(files, list) or not files or len(files) > 20:
            raise ComplianceError("compliance_license_file_missing")
        for item in files:
            if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
                raise ComplianceError("compliance_license_file_invalid")
            path = str(item["path"] or "")
            if (
                not path
                or len(path) > 300
                or not _SHA256.fullmatch(str(item["sha256"] or ""))
            ):
                raise ComplianceError("compliance_license_file_invalid")
            if not any(
                token in path.lower() for token in ("license", "copying", "notice")
            ):
                raise ComplianceError("compliance_license_file_name_invalid")

        exception = exception_map.get(key)
        if not isinstance(exception, dict):
            raise ComplianceError("compliance_policy_exception_missing")
        if (
            exception.get("owner") != owner
            or exception.get("expires_on") != expires.isoformat()
        ):
            raise ComplianceError("compliance_policy_exception_mismatch")
        if purl not in notice or license_id not in notice:
            raise ComplianceError("compliance_notice_missing")
        if source not in notice:
            raise ComplianceError("compliance_notice_source_missing")
        checked.append(
            {
                "package": package,
                "version": version,
                "purl": purl,
                "license": license_id,
                "owner": owner,
                "expires_on": expires.isoformat(),
                "source": source,
                "license_file_count": len(files),
                "modified": False,
                "replacement_supported": True,
            }
        )

    if set(exception_map) != seen:
        raise ComplianceError("compliance_policy_exception_set_mismatch")

    payload = {
        "schema_version": "nexus_container_license_compliance_evidence_v1",
        "status": "pass",
        "checked_count": len(checked),
        "components": checked,
    }
    output_path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compliance", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--installed", type=Path, required=True)
    parser.add_argument("--notice", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--today", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    try:
        return verify(
            compliance_path=args.compliance,
            policy_path=args.policy,
            sbom_path=args.sbom,
            installed_path=args.installed,
            notice_path=args.notice,
            output_path=args.output,
            today=args.today,
        )
    except (ComplianceError, OSError, UnicodeError) as exc:
        args.output.write_text(
            json.dumps(
                {
                    "schema_version": "nexus_container_license_compliance_evidence_v1",
                    "status": "fail",
                    "reason": str(exc)[:120],
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"license_compliance_error:{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
