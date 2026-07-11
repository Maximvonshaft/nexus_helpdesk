from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_COMPONENTS = 2000
_ALLOWED_PURL_PREFIXES = ("pkg:pypi/", "pkg:generic/python@")
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:/?&=@%~-]{0,499}$")
_SAFE_LICENSE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+() -]{0,199}$")
_LICENSE_ALIASES = {
    "apache license 2.0": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "mit license": "MIT",
    "bsd license": "BSD-3-Clause",
    "bsd-3-clause license": "BSD-3-Clause",
    "python software foundation license": "PSF-2.0",
    "psf license": "PSF-2.0",
    "mozilla public license 2.0": "MPL-2.0",
}


class SbomSanitizationError(ValueError):
    pass


def _load_json(path: Path) -> Any:
    if not path.is_file():
        raise SbomSanitizationError(f"missing_input:{path.name}")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise SbomSanitizationError(f"input_too_large:{path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SbomSanitizationError(f"invalid_json:{path.name}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _safe(value: Any, *, fallback: str = "unknown", limit: int = 500) -> str:
    text = str(value or "").strip()[:limit]
    return text if _SAFE_VALUE.fullmatch(text) else fallback


def _normalize_license(value: Any) -> str | None:
    text = " ".join(str(value or "").strip().split())
    if not text or not _SAFE_LICENSE.fullmatch(text):
        return None
    return _LICENSE_ALIASES.get(text.lower(), text)


def _component_licenses(component: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in component.get("licenses") or []:
        if not isinstance(entry, dict):
            continue
        expression = _normalize_license(entry.get("expression"))
        if expression:
            key = f"expression:{expression}"
            if key not in seen:
                seen.add(key)
                result.append({"expression": expression})
            continue
        info = entry.get("license")
        if not isinstance(info, dict):
            continue
        identifier = _normalize_license(info.get("id") or info.get("name"))
        if identifier and identifier not in seen:
            seen.add(identifier)
            result.append({"license": {"id": identifier}})
    return result


def _load_overrides(path: Path) -> dict[str, dict[str, str]]:
    payload = _load_json(path)
    if not isinstance(payload, dict) or payload.get("schema_version") != "nexus_container_license_metadata_overrides_v1":
        raise SbomSanitizationError("license_metadata_override_schema_invalid")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise SbomSanitizationError("license_metadata_override_entries_invalid")
    result: dict[str, dict[str, str]] = {}
    for raw in entries:
        if not isinstance(raw, dict):
            raise SbomSanitizationError("license_metadata_override_invalid")
        purl = _safe(raw.get("purl"))
        license_id = _normalize_license(raw.get("license"))
        source = str(raw.get("source") or "").strip()
        reason = " ".join(str(raw.get("reason") or "").strip().split())
        if not purl.startswith(_ALLOWED_PURL_PREFIXES) or not license_id:
            raise SbomSanitizationError("license_metadata_override_key_invalid")
        if not (source.startswith("https://") and 12 <= len(reason) <= 240):
            raise SbomSanitizationError("license_metadata_override_evidence_invalid")
        if purl in result:
            raise SbomSanitizationError("license_metadata_override_duplicate")
        result[purl] = {
            "license": license_id,
            "source": source[:500],
            "reason": reason,
        }
    return result


def sanitize_sbom(source: Path, overrides_path: Path, output: Path) -> int:
    payload = _load_json(source)
    if not isinstance(payload, dict) or str(payload.get("bomFormat") or "").lower() != "cyclonedx":
        raise SbomSanitizationError("cyclonedx_sbom_invalid")
    components = payload.get("components")
    if not isinstance(components, list):
        raise SbomSanitizationError("cyclonedx_components_invalid")
    if len(components) > 10000:
        raise SbomSanitizationError("cyclonedx_component_count_excessive")

    overrides = _load_overrides(overrides_path)
    applied: set[str] = set()
    unresolved: list[dict[str, str]] = []
    selected: dict[str, dict[str, Any]] = {}
    base_purls: list[str] = []

    for component in components:
        if not isinstance(component, dict):
            continue
        purl = _safe(component.get("purl"))
        if purl == "unknown":
            continue
        if not purl.startswith(_ALLOWED_PURL_PREFIXES):
            if purl.startswith("pkg:deb/"):
                base_purls.append(purl)
            continue
        name = _safe(component.get("name"), limit=200)
        version = _safe(component.get("version"), fallback="unversioned", limit=120)
        licenses = _component_licenses(component)
        override = overrides.get(purl)
        if not licenses and override:
            applied.add(purl)
            licenses = [{"license": {"id": override["license"]}}]
        if not licenses:
            unresolved.append({"purl": purl, "name": name, "version": version})
        selected[purl] = {
            "bom-ref": purl,
            "type": "library" if component.get("type") != "application" else "application",
            "name": name,
            "version": version,
            "purl": purl,
            "licenses": licenses,
        }

    if len(selected) > MAX_COMPONENTS:
        raise SbomSanitizationError("application_component_count_excessive")
    unused = sorted(set(overrides) - applied)
    status = "pass" if not unresolved and not unused else "fail"
    base_digest = hashlib.sha256("\n".join(sorted(set(base_purls))).encode("utf-8")).hexdigest()
    safe_payload = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "properties": [
                {"name": "nexus:source-sbom-sha256", "value": _sha256(source)},
                {"name": "nexus:source-component-count", "value": str(len(components))},
                {"name": "nexus:application-component-count", "value": str(len(selected))},
                {"name": "nexus:base-os-package-count", "value": str(len(set(base_purls)))},
                {"name": "nexus:base-os-purl-set-sha256", "value": "sha256:" + base_digest},
                {"name": "nexus:license-metadata-status", "value": status},
                {"name": "nexus:license-metadata-unresolved-count", "value": str(len(unresolved))},
                {"name": "nexus:license-metadata-applied-override-count", "value": str(len(applied))},
                {"name": "nexus:license-metadata-unused-override-count", "value": str(len(unused))},
            ]
        },
        "components": [selected[purl] for purl in sorted(selected)],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(safe_payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    summary_path = output.with_suffix(output.suffix + ".summary.json")
    summary_path.write_text(
        json.dumps(
            {
                "schema_version": "nexus_sanitized_image_sbom_v1",
                "status": status,
                "source_component_count": len(components),
                "application_component_count": len(selected),
                "base_os_package_count": len(set(base_purls)),
                "unresolved_count": len(unresolved),
                "applied_override_count": len(applied),
                "unused_override_count": len(unused),
                "unresolved": unresolved[:100],
                "unused_overrides": unused[:100],
                "source_sbom_sha256": _sha256(source),
                "sanitized_sbom_sha256": _sha256(output),
            },
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0 if status == "pass" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a minimal PII-free application dependency SBOM")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--overrides", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        return sanitize_sbom(args.input, args.overrides, args.output)
    except SbomSanitizationError as exc:
        print(f"sanitize_image_sbom_error:{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
