from __future__ import annotations

import argparse
import json
import re
import shutil
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
_SAFE_PYTHON_PURL = re.compile(
    r"^pkg:(?:pypi/[A-Za-z0-9._-]+|generic/python)@[A-Za-z0-9._+!-]{1,100}$"
)
_SAFE_NPM_PURL = re.compile(
    r"^pkg:npm/(?:%40[A-Za-z0-9._-]+/)?[A-Za-z0-9._-]+@[A-Za-z0-9._+!~-]{1,120}(?:\?[A-Za-z0-9._~%=&+-]{1,300})?$"
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


def _safe_purl(value: object) -> str:
    text = str(value or "").strip()
    if not (_SAFE_PYTHON_PURL.fullmatch(text) or _SAFE_NPM_PURL.fullmatch(text)):
        raise EvidenceError("purl_invalid")
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
    if value["bomFormat"] != "CycloneDX" or value["specVersion"] != "1.6" or value["version"] != 1:
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
    if not isinstance(components, list) or not components or len(components) > MAX_COMPONENTS:
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
        purl = _safe_purl(component["purl"])
        if component["bom-ref"] != purl or purl in seen:
            raise EvidenceError("sbom_purl_invalid")
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
                if not _SAFE_SPDX.fullmatch(str(entry["expression"] or "")):
                    raise EvidenceError("sbom_license_expression_invalid")
            elif set(entry) == {"license"} and isinstance(entry["license"], dict):
                _exact(entry["license"], {"id"}, "sbom_license")
                if not _SAFE_SPDX.fullmatch(str(entry["license"]["id"] or "")):
                    raise EvidenceError("sbom_license_id_invalid")
            else:
                raise EvidenceError("sbom_license_shape_invalid")


def validate_summary(value: object, schema: str, *, require_pass: bool = False) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != schema:
        raise EvidenceError(f"{schema}_invalid")
    if require_pass and value.get("status") != "pass":
        raise EvidenceError(f"{schema}_not_pass")
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
    if value["schema_version"] != "nexus_release_image_assurance_v1" or value["status"] != "pass":
        raise EvidenceError("manifest_schema_invalid")
    if not _SHA40.fullmatch(str(value["source_sha"])) or not _SHA256.fullmatch(str(value["image_id"])):
        raise EvidenceError("manifest_identity_invalid")
    for key in ("license_summary_sha256", "sbom_sha256", "vulnerability_summary_sha256"):
        if not _SHA256.fullmatch(str(value[key])):
            raise EvidenceError("manifest_digest_invalid")
    if value["image_pushed"] is not False or value["deployment_performed"] is not False:
        raise EvidenceError("manifest_external_effect_invalid")
    _walk_forbidden(value)


def validate_installed_evidence(value: object) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != "nexus_installed_license_evidence_v1":
        raise EvidenceError("installed_evidence_schema_invalid")
    components = value.get("components")
    if not isinstance(components, list) or not components or len(components) > 50:
        raise EvidenceError("installed_evidence_components_invalid")
    seen: set[str] = set()
    for component in components:
        if not isinstance(component, dict) or set(component) != {"purl", "package", "version", "license_files"}:
            raise EvidenceError("installed_evidence_component_invalid")
        purl = _safe_purl(component["purl"])
        if purl in seen:
            raise EvidenceError("installed_evidence_purl_duplicate")
        seen.add(purl)
        _safe(component["package"], limit=100)
        _safe(component["version"], limit=120)
        files = component["license_files"]
        if not isinstance(files, list) or not files or len(files) > 20:
            raise EvidenceError("installed_evidence_files_invalid")
        for item in files:
            if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
                raise EvidenceError("installed_evidence_file_invalid")
            _safe(item["path"], limit=300)
            if not _SHA256.fullmatch(str(item["sha256"] or "")):
                raise EvidenceError("installed_evidence_digest_invalid")
    _walk_forbidden(value)


def validate_binding(value: object, *, manifest: dict[str, Any]) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != "nexus_release_image_compliance_binding_v1":
        raise EvidenceError("binding_schema_invalid")
    if value.get("status") != "pass":
        raise EvidenceError("binding_not_pass")
    if value.get("source_sha") != manifest.get("source_sha") or value.get("image_id") != manifest.get("image_id"):
        raise EvidenceError("binding_identity_mismatch")
    if value.get("image_pushed") is not False or value.get("deployment_performed") is not False:
        raise EvidenceError("binding_external_effect_invalid")
    for key in (
        "base_manifest_sha256",
        "policy_input_validation_sha256",
        "license_compliance_sha256",
        "installed_license_evidence_sha256",
    ):
        if not _SHA256.fullmatch(str(value.get(key) or "")):
            raise EvidenceError("binding_digest_invalid")
    _walk_forbidden(value)


def _zero_marker(directory: Path, name: str) -> bool:
    marker = directory / name
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
    directory.joinpath("release-image-quarantine.json").write_text(
        json.dumps(
            {
                "schema_version": "nexus_release_image_quarantine_v1",
                "status": "quarantined",
                "reason": safe_reason or "validation_failed",
                "unsafe_artifacts_uploaded": False,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
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
        if not _zero_marker(directory, "raw-cleanup-exit-code"):
            raise EvidenceError("raw_cleanup_not_clean")
        if not _zero_marker(directory, "artifact-scan-exit-code"):
            raise EvidenceError("artifact_scan_not_clean")
        sbom_value = _load(sbom)
        manifest_value = _load(manifest)
        validate_sbom(sbom_value)
        validate_summary(_load(sbom_summary), "nexus_finalized_image_sbom_v1", require_pass=True)
        validate_raw_digests(_load(raw_digests))
        validate_summary(
            _load(vulnerabilities),
            "nexus_container_vulnerability_assurance_v1",
            require_pass=True,
        )
        validate_summary(
            _load(licenses),
            "nexus_container_license_assurance_v1",
            require_pass=True,
        )
        validate_manifest(manifest_value)
        validate_summary(
            _load(directory / "policy-input-validation.json"),
            "nexus_release_image_policy_input_validation_v1",
            require_pass=True,
        )
        validate_installed_evidence(_load(directory / "installed-license-evidence.json"))
        validate_summary(
            _load(directory / "license-compliance-evidence.json"),
            "nexus_container_license_compliance_evidence_v1",
            require_pass=True,
        )
        validate_binding(
            _load(directory / "release-image-compliance-binding.json"),
            manifest=manifest_value,
        )
    except Exception as exc:
        reason = str(exc)[:120] if isinstance(exc, EvidenceError) else f"unexpected_{type(exc).__name__}"
        _quarantine_artifacts(directory, reason=reason)
        return 1
    output.write_text(
        json.dumps(
            {
                "schema_version": "nexus_release_image_evidence_validation_v1",
                "status": "pass",
                "validated_files": 10,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
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
