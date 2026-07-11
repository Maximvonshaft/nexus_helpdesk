from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_FINDINGS = 200
MAX_EXCEPTION_DAYS = 180
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:/@-]{0,199}$")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_SPDX_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]*")


class AssuranceError(ValueError):
    pass


def _load_json(path: Path) -> Any:
    if not path.is_file():
        raise AssuranceError(f"missing_input:{path.name}")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise AssuranceError(f"input_too_large:{path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AssuranceError(f"invalid_json:{path.name}") from exc


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    if len(encoded.encode("utf-8")) > 256 * 1024:
        raise AssuranceError("output_too_large")
    path.write_text(encoded, encoding="utf-8")


def _safe_label(value: Any, *, fallback: str = "unknown", limit: int = 200) -> str:
    text = " ".join(str(value or "").strip().split())[:limit]
    return text if _SAFE_LABEL.fullmatch(text) else fallback


def _parse_expiry(value: Any, *, today: date) -> date:
    try:
        parsed = date.fromisoformat(str(value or ""))
    except ValueError as exc:
        raise AssuranceError("exception_invalid_expiry") from exc
    if parsed <= today:
        raise AssuranceError("exception_expired")
    if parsed > today + timedelta(days=MAX_EXCEPTION_DAYS):
        raise AssuranceError("exception_expiry_too_long")
    return parsed


def _reason(value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    if not 12 <= len(text) <= 240:
        raise AssuranceError("exception_reason_invalid")
    lowered = text.lower()
    if any(token in lowered for token in ("bearer ", "password", "secret=", "token=")):
        raise AssuranceError("exception_reason_sensitive")
    return text


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _vulnerability_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        _safe_label(item.get("VulnerabilityID")),
        _safe_label(item.get("PkgName")),
        _safe_label(item.get("InstalledVersion")),
    )


def _vulnerability_exceptions(path: Path, *, today: date) -> dict[tuple[str, str, str], dict[str, Any]]:
    payload = _load_json(path)
    if not isinstance(payload, dict) or payload.get("schema_version") != "nexus_container_vulnerability_exceptions_v1":
        raise AssuranceError("vulnerability_exception_schema_invalid")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise AssuranceError("vulnerability_exception_entries_invalid")
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    for raw in entries:
        if not isinstance(raw, dict):
            raise AssuranceError("vulnerability_exception_invalid")
        key = (
            _safe_label(raw.get("vulnerability_id")),
            _safe_label(raw.get("package")),
            _safe_label(raw.get("installed_version")),
        )
        if "unknown" in key or key in result:
            raise AssuranceError("vulnerability_exception_key_invalid")
        result[key] = {
            "reason": _reason(raw.get("reason")),
            "expires_on": _parse_expiry(raw.get("expires_on"), today=today).isoformat(),
            "owner": _safe_label(raw.get("owner"), fallback="unassigned", limit=80),
        }
    return result


def evaluate_vulnerabilities(report_path: Path, exceptions_path: Path, output_path: Path, *, today: date) -> int:
    report = _load_json(report_path)
    if not isinstance(report, dict):
        raise AssuranceError("trivy_report_invalid")
    exceptions = _vulnerability_exceptions(exceptions_path, today=today)
    findings: list[dict[str, Any]] = []
    applied: set[tuple[str, str, str]] = set()
    counts = {"CRITICAL": 0, "HIGH": 0}

    results = report.get("Results") or []
    if not isinstance(results, list):
        raise AssuranceError("trivy_results_invalid")
    for result in results:
        if not isinstance(result, dict):
            continue
        target = _safe_label(result.get("Target"), fallback="image", limit=120)
        vulnerabilities = result.get("Vulnerabilities") or []
        if not isinstance(vulnerabilities, list):
            continue
        for item in vulnerabilities:
            if not isinstance(item, dict):
                continue
            severity = str(item.get("Severity") or "").upper()
            if severity not in counts:
                continue
            counts[severity] += 1
            key = _vulnerability_key(item)
            exception = exceptions.get(key)
            if exception:
                applied.add(key)
                continue
            findings.append(
                {
                    "vulnerability_id": key[0],
                    "package": key[1],
                    "installed_version": key[2],
                    "fixed_version": _safe_label(item.get("FixedVersion"), fallback="unavailable"),
                    "severity": severity,
                    "target": target,
                }
            )

    unused = sorted(set(exceptions) - applied)
    status = "pass" if not findings and not unused else "fail"
    payload = {
        "schema_version": "nexus_container_vulnerability_assurance_v1",
        "status": status,
        "counts": counts,
        "unresolved_count": len(findings),
        "applied_exception_count": len(applied),
        "unused_exception_count": len(unused),
        "findings": findings[:MAX_FINDINGS],
        "findings_truncated": len(findings) > MAX_FINDINGS,
        "unused_exceptions": [
            {"vulnerability_id": key[0], "package": key[1], "installed_version": key[2]}
            for key in unused[:MAX_FINDINGS]
        ],
    }
    _write_json(output_path, payload)
    return 0 if status == "pass" else 1


def _license_tokens(value: str) -> list[str]:
    tokens = []
    skip_next = False
    for token in _SPDX_TOKEN.findall(value or ""):
        upper = token.upper()
        if upper in {"AND", "OR", "WITH"}:
            skip_next = upper == "WITH"
            continue
        if skip_next:
            skip_next = False
            continue
        tokens.append(token)
    return tokens


def _component_licenses(component: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for entry in component.get("licenses") or []:
        if not isinstance(entry, dict):
            continue
        expression = entry.get("expression")
        if isinstance(expression, str) and expression.strip():
            values.extend(_license_tokens(expression))
            continue
        license_info = entry.get("license")
        if isinstance(license_info, dict):
            value = license_info.get("id") or license_info.get("name")
            if isinstance(value, str) and value.strip():
                values.extend(_license_tokens(value))
    return list(dict.fromkeys(values)) or ["NOASSERTION"]


def _load_license_policy(path: Path, *, today: date) -> dict[str, Any]:
    payload = _load_json(path)
    if not isinstance(payload, dict) or payload.get("schema_version") != "nexus_container_license_policy_v1":
        raise AssuranceError("license_policy_schema_invalid")
    result = {
        "allowed": set(map(str, payload.get("allowed") or [])),
        "denied": set(map(str, payload.get("denied") or [])),
        "review": set(map(str, payload.get("review") or [])),
        "unknown_action": str(payload.get("unknown_action") or "review"),
        "exceptions": {},
    }
    if result["unknown_action"] not in {"allow", "review", "deny"}:
        raise AssuranceError("license_unknown_action_invalid")
    if (result["allowed"] & result["denied"]) or (result["allowed"] & result["review"]) or (result["denied"] & result["review"]):
        raise AssuranceError("license_policy_overlap")
    entries = payload.get("exceptions") or []
    if not isinstance(entries, list):
        raise AssuranceError("license_exception_entries_invalid")
    for raw in entries:
        if not isinstance(raw, dict):
            raise AssuranceError("license_exception_invalid")
        key = (
            _safe_label(raw.get("package")),
            _safe_label(raw.get("version")),
            _safe_label(raw.get("license")),
        )
        if "unknown" in key or key in result["exceptions"]:
            raise AssuranceError("license_exception_key_invalid")
        result["exceptions"][key] = {
            "reason": _reason(raw.get("reason")),
            "expires_on": _parse_expiry(raw.get("expires_on"), today=today).isoformat(),
            "owner": _safe_label(raw.get("owner"), fallback="unassigned", limit=80),
        }
    return result


def _license_disposition(license_id: str, policy: dict[str, Any]) -> str:
    if license_id in policy["allowed"]:
        return "allowed"
    if license_id in policy["denied"]:
        return "denied"
    if license_id in policy["review"]:
        return "review"
    return str(policy["unknown_action"])


def evaluate_licenses(sbom_path: Path, policy_path: Path, output_path: Path, *, today: date) -> int:
    sbom = _load_json(sbom_path)
    if not isinstance(sbom, dict) or str(sbom.get("bomFormat") or "").lower() != "cyclonedx":
        raise AssuranceError("cyclonedx_sbom_invalid")
    policy = _load_license_policy(policy_path, today=today)
    exceptions: dict[tuple[str, str, str], dict[str, Any]] = policy["exceptions"]
    findings: list[dict[str, Any]] = []
    applied: set[tuple[str, str, str]] = set()
    counts = {"components": 0, "allowed": 0, "review": 0, "denied": 0, "unknown": 0}

    components = sbom.get("components") or []
    if not isinstance(components, list):
        raise AssuranceError("cyclonedx_components_invalid")
    for component in components:
        if not isinstance(component, dict):
            continue
        counts["components"] += 1
        package = _safe_label(component.get("name"))
        version = _safe_label(component.get("version"), fallback="unversioned")
        for license_id in _component_licenses(component):
            normalized = _safe_label(license_id)
            disposition = _license_disposition(normalized, policy)
            if normalized == "NOASSERTION" or normalized not in policy["allowed"] | policy["denied"] | policy["review"]:
                counts["unknown"] += 1
            counts[disposition] = counts.get(disposition, 0) + 1
            if disposition == "allowed":
                continue
            key = (package, version, normalized)
            if key in exceptions:
                applied.add(key)
                continue
            findings.append(
                {
                    "package": package,
                    "version": version,
                    "license": normalized,
                    "disposition": disposition,
                }
            )

    unused = sorted(set(exceptions) - applied)
    status = "pass" if not findings and not unused else "fail"
    payload = {
        "schema_version": "nexus_container_license_assurance_v1",
        "status": status,
        "counts": counts,
        "unresolved_count": len(findings),
        "applied_exception_count": len(applied),
        "unused_exception_count": len(unused),
        "findings": findings[:MAX_FINDINGS],
        "findings_truncated": len(findings) > MAX_FINDINGS,
        "unused_exceptions": [
            {"package": key[0], "version": key[1], "license": key[2]}
            for key in unused[:MAX_FINDINGS]
        ],
    }
    _write_json(output_path, payload)
    return 0 if status == "pass" else 1


def build_manifest(
    *,
    source_sha: str,
    image_id: str,
    sbom_path: Path,
    vulnerability_summary_path: Path,
    license_summary_path: Path,
    output_path: Path,
) -> int:
    source = source_sha.strip().lower()
    image = image_id.strip().lower()
    if not _SHA40.fullmatch(source):
        raise AssuranceError("source_sha_invalid")
    if not _SHA256.fullmatch(image):
        raise AssuranceError("image_id_invalid")
    vulnerabilities = _load_json(vulnerability_summary_path)
    licenses = _load_json(license_summary_path)
    status = "pass" if vulnerabilities.get("status") == "pass" and licenses.get("status") == "pass" else "fail"
    payload = {
        "schema_version": "nexus_release_image_assurance_v1",
        "status": status,
        "source_sha": source,
        "image_id": image if image.startswith("sha256:") else "sha256:" + image,
        "sbom_sha256": _sha256(sbom_path),
        "vulnerability_summary_sha256": _sha256(vulnerability_summary_path),
        "license_summary_sha256": _sha256(license_summary_path),
        "vulnerability_status": vulnerabilities.get("status"),
        "license_status": licenses.get("status"),
        "critical_count": int((vulnerabilities.get("counts") or {}).get("CRITICAL") or 0),
        "high_count": int((vulnerabilities.get("counts") or {}).get("HIGH") or 0),
        "unresolved_license_count": int(licenses.get("unresolved_count") or 0),
        "image_pushed": False,
        "deployment_performed": False,
    }
    _write_json(output_path, payload)
    return 0 if status == "pass" else 1


def _today(value: str | None) -> date:
    return date.fromisoformat(value) if value else date.today()


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate Nexus release image security evidence")
    sub = parser.add_subparsers(dest="command", required=True)

    vuln = sub.add_parser("vulnerabilities")
    vuln.add_argument("--report", type=Path, required=True)
    vuln.add_argument("--exceptions", type=Path, required=True)
    vuln.add_argument("--output", type=Path, required=True)
    vuln.add_argument("--today")

    license_parser = sub.add_parser("licenses")
    license_parser.add_argument("--sbom", type=Path, required=True)
    license_parser.add_argument("--policy", type=Path, required=True)
    license_parser.add_argument("--output", type=Path, required=True)
    license_parser.add_argument("--today")

    manifest = sub.add_parser("manifest")
    manifest.add_argument("--source-sha", required=True)
    manifest.add_argument("--image-id", required=True)
    manifest.add_argument("--sbom", type=Path, required=True)
    manifest.add_argument("--vulnerabilities", type=Path, required=True)
    manifest.add_argument("--licenses", type=Path, required=True)
    manifest.add_argument("--output", type=Path, required=True)

    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "vulnerabilities":
            return evaluate_vulnerabilities(args.report, args.exceptions, args.output, today=_today(args.today))
        if args.command == "licenses":
            return evaluate_licenses(args.sbom, args.policy, args.output, today=_today(args.today))
        return build_manifest(
            source_sha=args.source_sha,
            image_id=args.image_id,
            sbom_path=args.sbom,
            vulnerability_summary_path=args.vulnerabilities,
            license_summary_path=args.licenses,
            output_path=args.output,
        )
    except AssuranceError as exc:
        print(f"release_image_assurance_error:{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
