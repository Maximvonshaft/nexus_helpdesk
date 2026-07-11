from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
MAX_BYTES = 512 * 1024


class BindingError(ValueError):
    pass


def _load(path: Path) -> Any:
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise BindingError(f"input_invalid:{path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BindingError(f"json_invalid:{path.name}") from exc


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def bind(
    *,
    source_sha: str,
    image_id: str,
    manifest_path: Path,
    compliance_path: Path,
    installed_path: Path,
    output_path: Path,
) -> int:
    source = source_sha.strip().lower()
    image = image_id.strip().lower()
    if not _SHA40.fullmatch(source) or not _SHA256.fullmatch(image):
        raise BindingError("candidate_identity_invalid")
    manifest = _load(manifest_path)
    compliance = _load(compliance_path)
    installed = _load(installed_path)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "nexus_release_image_assurance_v1":
        raise BindingError("manifest_schema_invalid")
    if manifest.get("source_sha") != source or manifest.get("image_id") != image:
        raise BindingError("manifest_identity_mismatch")
    if not isinstance(compliance, dict) or compliance.get("schema_version") != "nexus_container_license_compliance_evidence_v1":
        raise BindingError("compliance_schema_invalid")
    if not isinstance(installed, dict) or installed.get("schema_version") != "nexus_installed_license_evidence_v1":
        raise BindingError("installed_schema_invalid")
    status = "pass" if manifest.get("status") == "pass" and compliance.get("status") == "pass" else "fail"
    payload = {
        "schema_version": "nexus_release_image_compliance_binding_v1",
        "status": status,
        "source_sha": source,
        "image_id": image if image.startswith("sha256:") else "sha256:" + image,
        "base_manifest_sha256": _sha256(manifest_path),
        "license_compliance_sha256": _sha256(compliance_path),
        "installed_license_evidence_sha256": _sha256(installed_path),
        "image_pushed": False,
        "deployment_performed": False,
    }
    output_path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return 0 if status == "pass" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--compliance", type=Path, required=True)
    parser.add_argument("--installed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        return bind(
            source_sha=args.source_sha,
            image_id=args.image_id,
            manifest_path=args.manifest,
            compliance_path=args.compliance,
            installed_path=args.installed,
            output_path=args.output,
        )
    except BindingError as exc:
        print(f"release_image_compliance_binding_error:{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
