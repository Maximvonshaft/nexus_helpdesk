from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

MAX_BYTES = 4 * 1024 * 1024
MAX_COMPONENTS = 2000
_SAFE_PURL = re.compile(r"^pkg:(?:pypi/[A-Za-z0-9._-]+|generic/python)@[A-Za-z0-9._+!-]{1,100}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,199}$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!-]{0,119}$")
_SAFE_SPDX = re.compile(r"^[A-Za-z0-9.+-]+(?: (?:AND|OR) [A-Za-z0-9.+-]+)*$")

_ALIASES = {
    "apache software license 2.0": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "3-clause bsd license": "BSD-3-Clause",
    "bsd 3-clause license": "BSD-3-Clause",
    "bsd license": "BSD-3-Clause",
    "gnu lesser general public license v3 (lgplv3)": "LGPL-3.0-only",
    "lgplv3": "LGPL-3.0-only",
    "dual license": "BSD-3-Clause OR Apache-2.0",
    "python software foundation license": "PSF-2.0",
    "mit license": "MIT",
}


class FinalizationError(ValueError):
    pass


def _load(path: Path) -> Any:
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise FinalizationError(f"input_invalid:{path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FinalizationError(f"json_invalid:{path.name}") from exc


def _normalize(value: object) -> str | None:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return None
    normalized = _ALIASES.get(text.lower(), text)
    return normalized if _SAFE_SPDX.fullmatch(normalized) else None


def _licenses(component: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[str] = []
    for entry in component.get("licenses") or []:
        if not isinstance(entry, dict):
            continue
        raw = entry.get("expression")
        if raw is None and isinstance(entry.get("license"), dict):
            info = entry["license"]
            raw = info.get("id") or info.get("name")
        normalized = _normalize(raw)
        if normalized:
            values.append(normalized)
    result: list[dict[str, Any]] = []
    for value in dict.fromkeys(values):
        if " AND " in value or " OR " in value:
            result.append({"expression": value})
        else:
            result.append({"license": {"id": value}})
    return result


def _load_overrides(path: Path) -> dict[str, str]:
    payload = _load(path)
    if not isinstance(payload, dict) or payload.get("schema_version") != "nexus_container_license_metadata_overrides_v1":
        raise FinalizationError("override_schema_invalid")
    entries = payload.get("entries")
    if not isinstance(entries, list) or len(entries) > 100:
        raise FinalizationError("override_entries_invalid")
    result: dict[str, str] = {}
    for raw in entries:
        if not isinstance(raw, dict):
            raise FinalizationError("override_entry_invalid")
        purl = str(raw.get("purl") or "").strip()
        license_id = _normalize(raw.get("license"))
        source = str(raw.get("source") or "").strip()
        reason = " ".join(str(raw.get("reason") or "").strip().split())
        if not _SAFE_PURL.fullmatch(purl) or not license_id:
            raise FinalizationError("override_key_invalid")
        if not source.startswith("https://") or not 12 <= len(reason) <= 240:
            raise FinalizationError("override_evidence_invalid")
        if purl in result:
            raise FinalizationError("override_duplicate")
        result[purl] = license_id
    return result


def finalize(source: Path, overrides_path: Path, output: Path) -> int:
    payload = _load(source)
    if not isinstance(payload, dict) or payload.get("bomFormat") != "CycloneDX":
        raise FinalizationError("sbom_schema_invalid")
    components = payload.get("components")
    if not isinstance(components, list) or len(components) > MAX_COMPONENTS:
        raise FinalizationError("sbom_components_invalid")
    overrides = _load_overrides(overrides_path)
    applied: set[str] = set()
    unresolved: list[dict[str, str]] = []
    finalized: list[dict[str, Any]] = []

    for component in components:
        if not isinstance(component, dict):
            raise FinalizationError("sbom_component_invalid")
        purl = str(component.get("purl") or "").strip()
        name = str(component.get("name") or "").strip()
        version = str(component.get("version") or "").strip()
        if not _SAFE_PURL.fullmatch(purl) or not _SAFE_NAME.fullmatch(name) or not _SAFE_VERSION.fullmatch(version):
            raise FinalizationError("sbom_component_identity_invalid")
        licenses = _licenses(component)
        if purl in overrides:
            applied.add(purl)
            override_value = overrides[purl]
            licenses = (
                [{"expression": override_value}]
                if " AND " in override_value or " OR " in override_value
                else [{"license": {"id": override_value}}]
            )
        if not licenses:
            unresolved.append({"purl": purl, "name": name, "version": version})
        finalized.append(
            {
                "bom-ref": purl,
                "type": "application" if component.get("type") == "application" else "library",
                "name": name,
                "version": version,
                "purl": purl,
                "licenses": licenses,
            }
        )

    unused = sorted(set(overrides) - applied)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    properties = metadata.get("properties") if isinstance(metadata.get("properties"), list) else []
    safe_properties = [
        item
        for item in properties
        if isinstance(item, dict)
        and set(item) == {"name", "value"}
        and str(item.get("name") or "").startswith("nexus:")
    ][:32]
    status = "pass" if not unresolved and not unused else "fail"
    safe_properties = [
        item for item in safe_properties
        if item.get("name") not in {
            "nexus:license-metadata-status",
            "nexus:license-metadata-unresolved-count",
            "nexus:license-metadata-applied-override-count",
            "nexus:license-metadata-unused-override-count",
        }
    ]
    safe_properties.extend(
        [
            {"name": "nexus:license-metadata-status", "value": status},
            {"name": "nexus:license-metadata-unresolved-count", "value": str(len(unresolved))},
            {"name": "nexus:license-metadata-applied-override-count", "value": str(len(applied))},
            {"name": "nexus:license-metadata-unused-override-count", "value": str(len(unused))},
        ]
    )
    result = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {"properties": safe_properties},
        "components": sorted(finalized, key=lambda item: item["purl"]),
    }
    output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    summary = {
        "schema_version": "nexus_finalized_image_sbom_v1",
        "status": status,
        "component_count": len(finalized),
        "unresolved_count": len(unresolved),
        "unresolved": unresolved[:100],
        "applied_override_count": len(applied),
        "unused_override_count": len(unused),
        "unused_overrides": unused[:100],
    }
    output.with_suffix(output.suffix + ".summary.json").write_text(
        json.dumps(summary, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if status == "pass" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--overrides", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        return finalize(args.input, args.overrides, args.output)
    except FinalizationError as exc:
        print(f"finalize_image_sbom_error:{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
