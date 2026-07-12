from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import sys
from pathlib import Path

_SAFE_PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!-]{0,119}$")
_MAX_FILE_BYTES = 2 * 1024 * 1024


def _hash_file(path: Path) -> str:
    if not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES:
        raise ValueError("license_file_invalid")
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _pypi_purl(name: str, version: str) -> str:
    normalized_name = name.strip().lower().replace("_", "-")
    normalized_version = version.strip()
    if not _SAFE_PACKAGE.fullmatch(normalized_name) or not _SAFE_VERSION.fullmatch(
        normalized_version
    ):
        raise ValueError("installed_component_identity_invalid")
    return f"pkg:pypi/{normalized_name}@{normalized_version}"


def extract(packages: list[str]) -> dict[str, object]:
    components: list[dict[str, object]] = []
    seen_purls: set[str] = set()
    for raw_name in packages:
        name = str(raw_name or "").strip()
        if not _SAFE_PACKAGE.fullmatch(name):
            raise ValueError("package_name_invalid")
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise ValueError(f"package_not_installed:{name}") from exc
        version = str(distribution.version or "").strip()
        purl = _pypi_purl(name, version)
        if purl in seen_purls:
            raise ValueError("installed_component_purl_duplicate")
        seen_purls.add(purl)
        files: list[dict[str, str]] = []
        for relative in distribution.files or []:
            relative_text = str(relative)
            basename = Path(relative_text).name.lower()
            if not any(
                token in basename for token in ("license", "copying", "notice")
            ):
                continue
            resolved = Path(distribution.locate_file(relative)).resolve()
            files.append(
                {
                    "path": relative_text[:300],
                    "sha256": _hash_file(resolved),
                }
            )
        files.sort(key=lambda item: item["path"])
        components.append(
            {
                "purl": purl,
                "package": name,
                "version": version[:120],
                "license_files": files[:20],
            }
        )
    return {
        "schema_version": "nexus_installed_license_evidence_v1",
        "components": components,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", action="append", dest="packages", required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    try:
        payload = extract(args.packages)
    except (ValueError, OSError) as exc:
        print(f"installed_license_evidence_error:{exc}", file=sys.stderr)
        return 1
    encoded = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        sys.stdout.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
