#!/usr/bin/env python3
"""Validate and cryptographically bind a Nexus RC test candidate manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

SCHEMA = "nexus.osr.rc-test-candidate.v1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
MIGRATION_RE = re.compile(r"^[0-9]{8}_[0-9]{4}$")
IMAGE_DIGEST_RE = re.compile(r"^[A-Za-z0-9._:/-]+@sha256:[0-9a-f]{64}$")
MAX_MANIFEST_BYTES = 256 * 1024
MAX_EVIDENCE_BYTES = 512 * 1024

REQUIRED_CHECKS = (
    "image_build",
    "compose_validation",
    "migration",
    "application_ready",
    "workers_healthy",
    "http_core_smoke",
    "browser_smoke",
    "side_effect_safety",
    "network_isolation",
    "teardown",
)
REQUIRED_FALSE_SAFETY = (
    "production_data_used",
    "production_network_joined",
    "provider_candidate_enabled",
    "real_outbound_enabled",
    "whatsapp_enabled",
    "speedaf_write_enabled",
    "operations_dispatch_enabled",
    "production_ready",
)
REQUIRED_EVIDENCE = (
    "health",
    "readiness",
    "http_core_smoke",
    "browser_smoke",
    "workers",
    "migration",
    "migration_head",
    "migration_current",
    "seed_first",
    "seed_second",
    "seed_verification",
    "side_effect_safety",
    "network_safety",
    "safe_config",
    "teardown",
    "rollback_verification",
)


class ManifestError(ValueError):
    pass


def _require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{field} must be an object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _resolve_evidence_path(manifest_path: Path, logical_name: str, entry: dict[str, Any]) -> Path:
    raw_path = entry.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ManifestError(f"evidence.{logical_name}.path is required")
    if raw_path.startswith("/") or "\\" in raw_path:
        raise ManifestError(f"evidence.{logical_name}.path must be a portable relative path")
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts or len(relative.parts) != 1:
        raise ManifestError(f"evidence.{logical_name}.path must stay directly inside the evidence root")
    root = manifest_path.parent.resolve(strict=True)
    candidate = (root / relative).resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ManifestError(f"evidence.{logical_name}.path escapes the evidence root") from exc
    if candidate.is_symlink() or not candidate.is_file():
        raise ManifestError(f"evidence.{logical_name}.path must be a regular non-symlink file")
    return candidate


def validate_manifest(payload: dict[str, Any], manifest_path: Path) -> None:
    if payload.get("schema") != SCHEMA:
        raise ManifestError(f"schema must be {SCHEMA}")
    if payload.get("release_class") != "controlled_test_deployment":
        raise ManifestError("release_class must be controlled_test_deployment")
    if payload.get("decision") != "RC0_TEST_DEPLOYABLE":
        raise ManifestError("decision must be RC0_TEST_DEPLOYABLE")

    candidate = _require_mapping(payload.get("candidate"), "candidate")
    source_sha = candidate.get("source_sha")
    if not isinstance(source_sha, str) or not SHA_RE.fullmatch(source_sha):
        raise ManifestError("candidate.source_sha must be a lowercase 40-character Git SHA")
    if candidate.get("frontend_build_sha") != source_sha:
        raise ManifestError("candidate.frontend_build_sha must match candidate.source_sha")
    if not isinstance(candidate.get("image_tag"), str) or not candidate["image_tag"].strip():
        raise ManifestError("candidate.image_tag is required")
    if not isinstance(candidate.get("image_id"), str) or not SHA256_RE.fullmatch(candidate["image_id"]):
        raise ManifestError("candidate.image_id must use exact sha256:<64 hex> form")
    if not isinstance(candidate.get("migration_revision"), str) or not MIGRATION_RE.fullmatch(
        candidate["migration_revision"]
    ):
        raise ManifestError("candidate.migration_revision must be one canonical Alembic revision")
    if candidate.get("config_profile") != "rc-test-isolated-v1":
        raise ManifestError("candidate.config_profile must be rc-test-isolated-v1")
    if not isinstance(candidate.get("config_digest"), str) or not SHA256_RE.fullmatch(
        candidate["config_digest"]
    ):
        raise ManifestError("candidate.config_digest must use exact sha256:<64 hex> form")
    for field in ("postgres_image_digest", "nginx_image_digest"):
        value = candidate.get(field)
        if not isinstance(value, str) or not IMAGE_DIGEST_RE.fullmatch(value):
            raise ManifestError(f"candidate.{field} must bind an OCI sha256 RepoDigest")

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
    missing_evidence = sorted(set(REQUIRED_EVIDENCE) - set(evidence))
    unexpected_evidence = sorted(set(evidence) - set(REQUIRED_EVIDENCE))
    if missing_evidence:
        raise ManifestError("missing evidence: " + ", ".join(missing_evidence))
    if unexpected_evidence:
        raise ManifestError("unexpected evidence: " + ", ".join(unexpected_evidence))

    used_paths: set[Path] = set()
    for logical_name in REQUIRED_EVIDENCE:
        entry = _require_mapping(evidence[logical_name], f"evidence.{logical_name}")
        path = _resolve_evidence_path(manifest_path, logical_name, entry)
        if path in used_paths:
            raise ManifestError(f"evidence.{logical_name} reuses another evidence file")
        used_paths.add(path)
        size = path.stat().st_size
        if size <= 0 or size > MAX_EVIDENCE_BYTES:
            raise ManifestError(f"evidence.{logical_name} size is outside the bounded range")
        if entry.get("size_bytes") != size:
            raise ManifestError(f"evidence.{logical_name}.size_bytes mismatch")
        digest = entry.get("sha256")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise ManifestError(f"evidence.{logical_name}.sha256 must use exact sha256:<64 hex> form")
        if digest != _sha256(path):
            raise ManifestError(f"evidence.{logical_name}.sha256 mismatch")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ManifestError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ManifestError(f"manifest not found or not regular: {path}")
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ManifestError("manifest exceeds 256 KiB")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
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
        manifest_path = args.manifest.resolve(strict=True)
        payload = load_manifest(manifest_path)
        validate_manifest(payload, manifest_path)
    except (ManifestError, OSError) as exc:
        print(f"RC_MANIFEST_VALID=false reason={exc}")
        return 2
    print("RC_MANIFEST_VALID=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
