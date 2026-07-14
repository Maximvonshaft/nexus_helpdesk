#!/usr/bin/env python3
"""Scan final controlled-candidate evidence without weakening secret/PII gates.

The repository-wide scanner remains authoritative. This wrapper suppresses only
PII fingerprints caused by exact, schema-validated release identifiers at
specific JSON paths. Secret, token, forbidden-key, and unrecognized PII
findings are never suppressed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

SECURITY_DIR = Path(__file__).resolve().parents[1] / "security"
if str(SECURITY_DIR) not in sys.path:
    sys.path.insert(0, str(SECURITY_DIR))

from scanner import (  # noqa: E402
    Finding,
    _PII_PATTERNS,
    _fingerprint,
    bounded_report,
    scan_artifact_files,
    write_report,
)

FINAL_PREFIX = "artifacts/final-controlled-candidate/"
FINAL_MANIFEST = FINAL_PREFIX + "controlled-candidate-manifest.json"
RC_MANIFEST = FINAL_PREFIX + "candidate-manifest.json"
PUBLISH_RECEIPT = FINAL_PREFIX + "registry-publish-receipt.json"

ATTESTATION_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
BUILD_TIME_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
GENERATED_AT_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)
APP_VERSION_RE = re.compile(r"^controlled-[0-9a-f]{12}$")
IMAGE_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*:rc-test-[0-9a-f]{40}$")


def _load_json(root: Path, relative: str) -> dict[str, object] | None:
    path = root / relative
    if not path.is_file() or path.is_symlink() or path.suffix.lower() != ".json":
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _attestation_url_is_bound(value: str, attestation_id: str) -> bool:
    if len(value) > 500 or any(character in value for character in "\x00\r\n"):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "github.com"
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return False
    parts = [part for part in parsed.path.split("/") if part]
    return (
        len(parts) == 4
        and parts[2] == "attestations"
        and parts[3] == attestation_id
        and all(re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", part) for part in parts[:2])
    )


def _safe_value(
    *,
    relative: str,
    schema: str,
    key_path: tuple[str, ...],
    value: str,
    payload: dict[str, object],
) -> bool:
    if relative == FINAL_MANIFEST and schema == "nexus.osr.controlled-candidate-manifest.v1":
        if key_path == ("generated_at",):
            return bool(GENERATED_AT_RE.fullmatch(value))
        if key_path == ("candidate", "build_time"):
            return bool(BUILD_TIME_RE.fullmatch(value))
        if key_path == ("candidate", "app_version"):
            return bool(APP_VERSION_RE.fullmatch(value))
        if key_path == ("candidate", "embedded_image_tag"):
            return bool(IMAGE_TAG_RE.fullmatch(value))
        attestation = payload.get("attestation")
        if not isinstance(attestation, dict):
            return False
        attestation_id = str(attestation.get("id") or "").strip()
        if key_path == ("attestation", "id"):
            return bool(ATTESTATION_ID_RE.fullmatch(value))
        if key_path == ("attestation", "url"):
            return bool(ATTESTATION_ID_RE.fullmatch(attestation_id)) and _attestation_url_is_bound(
                value, attestation_id
            )
        return False

    if relative == RC_MANIFEST and schema == "nexus.osr.rc-test-candidate.v1":
        return key_path == ("candidate", "image_tag") and bool(IMAGE_TAG_RE.fullmatch(value))

    if relative == PUBLISH_RECEIPT and schema == "nexus.osr.registry-publish-receipt.v1":
        if key_path == ("build_time",):
            return bool(BUILD_TIME_RE.fullmatch(value))
        if key_path == ("app_version",):
            return bool(APP_VERSION_RE.fullmatch(value))
        if key_path == ("embedded_image_tag",):
            return bool(IMAGE_TAG_RE.fullmatch(value))

    return False


def _collect_safe_pii_fingerprints(
    value: object,
    *,
    relative: str,
    schema: str,
    payload: dict[str, object],
    key_path: tuple[str, ...] = (),
    depth: int = 0,
) -> set[tuple[str, str, str]]:
    if depth > 10:
        return set()
    output: set[tuple[str, str, str]] = set()
    if isinstance(value, dict):
        for raw_key, child in list(value.items())[:200]:
            output.update(
                _collect_safe_pii_fingerprints(
                    child,
                    relative=relative,
                    schema=schema,
                    payload=payload,
                    key_path=key_path + (str(raw_key).strip().lower(),),
                    depth=depth + 1,
                )
            )
    elif isinstance(value, list):
        for child in value[:200]:
            output.update(
                _collect_safe_pii_fingerprints(
                    child,
                    relative=relative,
                    schema=schema,
                    payload=payload,
                    key_path=key_path,
                    depth=depth + 1,
                )
            )
    elif isinstance(value, str) and _safe_value(
        relative=relative,
        schema=schema,
        key_path=key_path,
        value=value,
        payload=payload,
    ):
        for rule, pattern in _PII_PATTERNS:
            match = pattern.search(value)
            if match:
                output.add(
                    (
                        relative,
                        f"artifact:{rule}",
                        _fingerprint(rule, relative, 0, match.group(0)),
                    )
                )
    return output


def scan_controlled_candidate_files(
    root: Path, relative_paths: Iterable[str]
) -> tuple[list[Finding], int]:
    paths = sorted(set(relative_paths))
    findings = scan_artifact_files(root, paths)
    safe: set[tuple[str, str, str]] = set()
    for relative in paths:
        if not relative.startswith(FINAL_PREFIX):
            continue
        payload = _load_json(root, relative)
        if payload is None:
            continue
        schema = str(payload.get("schema") or payload.get("schema_version") or "")
        safe.update(
            _collect_safe_pii_fingerprints(
                payload,
                relative=relative,
                schema=schema,
                payload=payload,
            )
        )
    remaining = [
        finding
        for finding in findings
        if (finding.path, finding.rule, finding.fingerprint) not in safe
    ]
    return remaining, len(findings) - len(remaining)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", required=True)
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    findings, suppressed = scan_controlled_candidate_files(root, args.paths)
    write_report(
        Path(args.output),
        bounded_report(
            schema="nexus_security_artifact_scan_v1",
            findings=findings,
            scanned_files=len(args.paths),
            suppressed_count=suppressed,
        ),
    )
    if findings:
        return 1
    print(
        "CONTROLLED_CANDIDATE_ARTIFACT_SCAN_VALID=true "
        f"files={len(args.paths)} technical_pii_suppressed={suppressed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
