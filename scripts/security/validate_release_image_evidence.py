from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

MAX_BYTES = 2 * 1024 * 1024
MAX_COMPONENTS = 2000
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:/@%?&=!-]{0,499}$")
_SAFE_COMPONENT_NAME = re.compile(
    r"^[A-Za-z0-9@][A-Za-z0-9._+:/@%?&=!-]{0,199}$"
)
_SAFE_SPDX = re.compile(
    r"^[A-Za-z0-9.+-]+(?: (?:AND|OR) [A-Za-z0-9.+-]+)*$"
)
_FORBIDDEN_KEYS = {
    "address",
    "api_key",
    "authorization",
    "body",
    "content",
    "credential",
    "customer",
    "email",
    "message",
    "password",
    "payload",
    "phone",
    "prompt",
    "raw_payload",
    "secret",
    "text",
    "token",
    "tracking_number",
    "transcript",
}
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{24,}\b", re.I),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    re.compile(r"\b\d{10,24}@g\.us\b", re.I),
)


class EvidenceError(ValueError):
    pass


def _load(path: Path) -> Any:
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise EvidenceError(f"input_invalid:{path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"json_invalid:{path.name}") from exc


def _exact(value: dict[str, Any], keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise EvidenceError(f"{label}_keys_invalid")


def _safe(value: object, *, limit: int = 500) -> str:
    text = str(value or "")
    if not text or len(text) > limit or not _SAFE_ID.fullmatch(text):
        raise EvidenceError("metadata_value_invalid")
    return text


def _safe_component_name(value: object) -> str:
    text = str(value or "")
    if not _SAFE_COMPONENT_NAME.fullmatch(text):
        raise EvidenceError("sbom_component_name_invalid")
    return text


def _walk_forbidden(value: object, *, depth: int = 0) -> None:
    if depth > 8:
        raise EvidenceError("evidence_depth_excessive")
    if isinstance(value, dict):
        if len(value) > 200:
            raise EvidenceError("evidence_object_excessive")
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_KEYS:
                raise EvidenceError(f"forbidden_key:{normalized}")
            _walk_forbidden(child, depth=depth + 1)
    elif isinstance(value, list):
        if len(value) > MAX_COMPONENTS:
            raise EvidenceError("evidence_list_excessive")
        for child in value:
            _walk_forbidden(child, depth=depth + 1)
    elif isinstance(value, str):
        if len(value) > 1000:
            raise EvidenceError("evidence_string_excessive")
        if any(pattern.search(value) for pattern in _SECRET_PATTERNS):
            raise EvidenceError("evidence_sensitive_value")


def validate_sbom(value: object) -> None:
    if not isinstance(value, dict):
        raise EvidenceError("sbom_not_object")
    _exact(
        value,
        {"bomFormat", "specVersion", "version", "metadata", "components"},
        "sbom",
    )
    if (
        value["bomFormat"] != "CycloneDX"
        or value["specVersion"] != "1.6"
        or value["version"] != 1
    ):
        raise EvidenceError("sbom_identity_invalid")
    metadata = value["metadata"]
    if not isinstance(metadata, dict):
        raise EvidenceError("sbom_metadata_invalid")
    _exact(metadata, {"properties"}, "sbom_metadata")
    properties = metadata["properties"]
    if not isinstance(properties, list) or len(properties) > 32:
        raise EvidenceError("sbom_properties_invalid")
    for item in properties:
        if not isinstance(item, dict):
            raise EvidenceError("sbom_property_invalid")
        _exact(item, {"name", "value"}, "sbom_property")
        name = _safe(item["name"], limit=120)
        value_text = _safe(item["value"], limit=500)
        if not name.startswith("nexus:"):
            raise EvidenceError("sbom_property_namespace_invalid")
        if name.endswith("sha256") and not _SHA256.fullmatch(value_text):
            raise EvidenceError("sbom_property_digest_invalid")
    components = value["components"]
    if not isinstance(components, list) or len(components) > MAX_COMPONENTS:
        raise EvidenceError("sbom_components_invalid")
    seen: set[str] = set()
    for component in components:
        if not isinstance(component, dict):
            raise EvidenceError("sbom_component_invalid")
        _exact(
            component,
            {"bom-ref", "type", "name", "version", "purl", "licenses"},
            "sbom_component",
        )
        purl = _safe(component["purl"])
        if (
            not purl.startswith(
                ("pkg:pypi/", "pkg:generic/python@", "pkg:npm/")
            )
            or component["bom-ref"] != purl
        ):
            raise EvidenceError("sbom_purl_invalid")
        if purl in seen:
            raise EvidenceError("sbom_purl_duplicate")
        seen.add(purl)
        if component["type"] not in {"library", "application"}:
            raise EvidenceError("sbom_component_type_invalid")
        _safe_component_name(component["name"])
        _safe(component["version"], limit=120)
        licenses = component["licenses"]
        if not isinstance(licenses, list) or not licenses or len(licenses) > 8:
            raise EvidenceError("sbom_license_missing")
        for entry in licenses:
            if not isinstance(entry, dict):
                raise EvidenceError("sbom_license_invalid")
            if set(entry) == {"expression"}:
                expression = str(entry["expression"] or "")
                if not _SAFE_SPDX.fullmatch(expression):
                    raise EvidenceError("sbom_license_expression_invalid")
            elif set(entry) == {"license"} and isinstance(
                entry["license"], dict
            ):
                _exact(entry["license"], {"id"}, "sbom_license")
                if not _SAFE_SPDX.fullmatch(
                    str(entry["license"]["id"] or "")
                ):
                    raise EvidenceError("sbom_license_id_invalid")
            else:
                raise EvidenceError("sbom_license_shape_invalid")


def validate_summary(value: object, schema: str) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != schema:
        raise EvidenceError(f"{schema}_invalid")
    _walk_forbidden(value)


def validate_raw_digests(value: object) -> None:
    if not isinstance(value, dict):
        raise EvidenceError("raw_digest_not_object")
    _exact(
        value,
        {
            "schema_version",
            "trivy_report_sha256",
            "raw_cyclonedx_sha256",
            "raw_frontend_cyclonedx_sha256",
        },
        "raw_digest",
    )
    if value["schema_version"] != "nexus_raw_release_evidence_digests_v2":
        raise EvidenceError("raw_digest_schema_invalid")
    for key in (
        "trivy_report_sha256",
        "raw_cyclonedx_sha256",
        "raw_frontend_cyclonedx_sha256",
    ):
        if not _SHA256.fullmatch(str(value[key])):
            raise EvidenceError("raw_digest_value_invalid")


def validate_manifest(value: object) -> None:
    if not isinstance(value, dict):
        raise EvidenceError("manifest_not_object")
    expected = {
        "critical_count",
        "deployment_performed",
        "high_count",
        "image_id",
        "image_pushed",
        "license_status",
        "license_summary_sha256",
        "sbom_sha256",
        "schema_version",
        "source_sha",
        "status",
        "unresolved_license_count",
        "vulnerability_status",
        "vulnerability_summary_sha256",
    }
    _exact(value, expected, "manifest")
    if value["schema_version"] != "nexus_release_image_assurance_v1":
        raise EvidenceError("manifest_schema_invalid")
    if not _SHA40.fullmatch(str(value["source_sha"])) or not _SHA256.fullmatch(
        str(value["image_id"])
    ):
        raise EvidenceError("manifest_identity_invalid")
    for key in (
        "license_summary_sha256",
        "sbom_sha256",
        "vulnerability_summary_sha256",
    ):
        if not _SHA256.fullmatch(str(value[key])):
            raise EvidenceError("manifest_digest_invalid")
    if (
        value["image_pushed"] is not False
        or value["deployment_performed"] is not False
    ):
        raise EvidenceError("manifest_external_effect_invalid")
    _walk_forbidden(value)


def _artifact_scan_is_clean(directory: Path) -> bool:
    marker = directory / "artifact-scan-exit-code"
    if not marker.is_file() or marker.stat().st_size > 16:
        return False
    try:
        return marker.read_text(encoding="utf-8").strip() == "0"
    except (OSError, UnicodeError):
        return False


def _quarantine_artifacts(directory: Path, *, reason: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for child in tuple(directory.iterdir()):
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)
    safe_reason = re.sub(r"[^A-Za-z0-9_.:-]", "_", reason)[:120]
    payload = {
        "schema_version": "nexus_release_image_quarantine_v1",
        "status": "quarantined",
        "reason": safe_reason or "validation_failed",
        "unsafe_artifacts_uploaded": False,
    }
    directory.joinpath("release-image-quarantine.json").write_text(
        json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
    )
    directory.joinpath("structured-evidence-scan.json").write_text(
        json.dumps(
            {
                "schema_version": "nexus_release_image_evidence_validation_v1",
                "status": "fail",
                "reason": safe_reason or "validation_failed",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def validate_evidence_set(
    *,
    sbom: Path,
    sbom_summary: Path,
    raw_digests: Path,
    vulnerabilities: Path,
    licenses: Path,
    manifest: Path,
    output: Path,
) -> int:
    directory = output.parent
    try:
        if not _artifact_scan_is_clean(directory):
            raise EvidenceError("artifact_scan_not_clean")
        validate_sbom(_load(sbom))
        validate_summary(
            _load(sbom_summary), "nexus_finalized_image_sbom_v1"
        )
        validate_raw_digests(_load(raw_digests))
        validate_summary(
            _load(vulnerabilities),
            "nexus_container_vulnerability_assurance_v1",
        )
        validate_summary(
            _load(licenses), "nexus_container_license_assurance_v1"
        )
        validate_manifest(_load(manifest))
    except Exception as exc:  # Fail closed and quarantine before upload.
        reason = (
            str(exc)[:120]
            if isinstance(exc, EvidenceError)
            else f"unexpected_{type(exc).__name__}"
        )
        _quarantine_artifacts(directory, reason=reason)
        return 1
    payload = {
        "schema_version": "nexus_release_image_evidence_validation_v1",
        "status": "pass",
        "validated_files": 6,
    }
    output.write_text(
        json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--sbom-summary", type=Path, required=True)
    parser.add_argument("--raw-digests", type=Path, required=True)
    parser.add_argument("--vulnerabilities", type=Path, required=True)
    parser.add_argument("--licenses", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    return validate_evidence_set(
        sbom=args.sbom,
        sbom_summary=args.sbom_summary,
        raw_digests=args.raw_digests,
        vulnerabilities=args.vulnerabilities,
        licenses=args.licenses,
        manifest=args.manifest,
        output=args.output,
    )


if __name__ == "__main__":
    raise SystemExit(main())
