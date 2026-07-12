#!/usr/bin/env python3
"""Validate a bounded Nexus RC test candidate manifest."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

SCHEMA = "nexus.osr.rc-test-candidate.v1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_CHECKS = (
    "image_build",
    "compose_validation",
    "migration",
    "application_ready",
    "workers_healthy",
    "http_core_smoke",
    "browser_smoke",
    "side_effect_safety",
    "teardown",
)
REQUIRED_FALSE_SAFETY = (
    "production_data_used",
    "production_network_joined",
    "provider_candidate_enabled",
    "real_outbound_enabled",
    "whatsapp_enabled",
    "speedaf_write_enabled",
    "production_ready",
)


class ManifestError(ValueError):
    pass


def _require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{field} must be an object")
    return value


def validate_manifest(payload: dict[str, Any]) -> None:
    if payload.get("schema") != SCHEMA:
        raise ManifestError(f"schema must be {SCHEMA}")
    if payload.get("release_class") != "controlled_test_deployment":
        raise ManifestError("release_class must be controlled_test_deployment")
    if payload.get("decision") != "RC0_TEST_DEPLOYABLE":
        raise ManifestError("decision must be RC0_TEST_DEPLOYABLE")

    candidate = _require_mapping(payload.get("candidate"), "candidate")
    source_sha = candidate.get("source_sha")
    frontend_sha = candidate.get("frontend_build_sha")
    if not isinstance(source_sha, str) or not SHA_RE.fullmatch(source_sha):
        raise ManifestError("candidate.source_sha must be a lowercase 40-character Git SHA")
    if frontend_sha != source_sha:
        raise ManifestError("candidate.frontend_build_sha must match candidate.source_sha")
    for field in ("image_tag", "image_id", "migration_revision", "config_profile", "config_digest"):
        value = candidate.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ManifestError(f"candidate.{field} is required")
    if not str(candidate["config_digest"]).startswith("sha256:"):
        raise ManifestError("candidate.config_digest must use sha256:<hex> form")

    checks = _require_mapping(payload.get("checks"), "checks")
    missing_checks = [name for name in REQUIRED_CHECKS if checks.get(name) != "pass"]
    if missing_checks:
        raise ManifestError(f"required checks are not pass: {', '.join(missing_checks)}")

    safety = _require_mapping(payload.get("safety"), "safety")
    bad_safety = [name for name in REQUIRED_FALSE_SAFETY if safety.get(name) is not False]
    if bad_safety:
        raise ManifestError(f"safety fields must be false: {', '.join(bad_safety)}")
    if safety.get("full_osr_automation") != "NO_GO":
        raise ManifestError("safety.full_osr_automation must remain NO_GO")
    if safety.get("test_environment_isolated") is not True:
        raise ManifestError("safety.test_environment_isolated must be true")

    evidence = _require_mapping(payload.get("evidence"), "evidence")
    if not evidence:
        raise ManifestError("evidence must not be empty")
    for name, value in evidence.items():
        if not isinstance(name, str) or not name:
            raise ManifestError("evidence keys must be non-empty strings")
        if not isinstance(value, str) or not value or value.startswith("/"):
            raise ManifestError(f"evidence.{name} must be a relative bounded path")


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ManifestError(f"manifest not found: {path}")
    if path.stat().st_size > 256 * 1024:
        raise ManifestError("manifest exceeds 256 KiB")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"manifest is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ManifestError("manifest root must be an object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        payload = load_manifest(args.manifest)
        validate_manifest(payload)
    except ManifestError as exc:
        print(f"RC_MANIFEST_VALID=false reason={exc}")
        return 2
    print("RC_MANIFEST_VALID=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
