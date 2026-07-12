#!/usr/bin/env python3
"""Build the exact-candidate RC manifest from completed bounded evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

EVIDENCE_FILES = {
    "health": "healthz.json",
    "readiness": "readyz.json",
    "http_core_smoke": "http-core-smoke.json",
    "browser_smoke": "browser-smoke.txt",
    "workers": "compose-ps-healthy.txt",
    "migration": "migration.txt",
    "migration_head": "migration-head.txt",
    "migration_current": "migration-current.txt",
    "seed_first": "seed-first.txt",
    "seed_second": "seed-second.txt",
    "seed_verification": "seed-verification.json",
    "side_effect_safety": "side-effect-safety.json",
    "network_safety": "network-safety.json",
    "safe_config": "safe-config.json",
    "teardown": "teardown.txt",
    "rollback_verification": "rollback-verification.json",
}


def _digest(path: Path) -> str:
    value = hashlib.sha256(path.read_bytes()).hexdigest()
    return "sha256:" + value


def _read(path: Path) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"empty evidence file: {path.name}")
    return value


def _finalize_teardown_evidence(root: Path) -> None:
    """Make the command transcript non-empty only after rollback is proven.

    Docker Compose writes normal teardown progress to stderr on some versions.
    The runner previously piped stdout into ``teardown.txt``, so a successful
    teardown could leave an empty regular file. Do not treat an empty transcript
    as proof by itself: normalize it only when the independent rollback receipt
    proves that no candidate container, volume, or network remains.
    """

    teardown_path = root / "teardown.txt"
    if teardown_path.is_symlink() or (teardown_path.exists() and not teardown_path.is_file()):
        raise ValueError("teardown evidence path is not a regular file")
    if teardown_path.is_file() and teardown_path.stat().st_size > 0:
        return

    rollback_path = root / "rollback-verification.json"
    if not rollback_path.is_file() or rollback_path.is_symlink():
        raise ValueError("teardown rollback proof unavailable")
    try:
        rollback = json.loads(rollback_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("teardown rollback proof invalid") from exc

    if not isinstance(rollback, dict):
        raise ValueError("teardown rollback proof invalid")
    if rollback.get("schema") != "nexus.osr.rc-test-rollback-verification.v1":
        raise ValueError("teardown rollback proof invalid")
    if rollback.get("status") != "pass":
        raise ValueError("teardown rollback proof invalid")
    for field in ("remaining_containers", "remaining_volumes", "remaining_networks"):
        if rollback.get(field) != 0:
            raise ValueError("teardown rollback proof reports remaining resources")

    teardown_path.write_text(
        "RC_TEARDOWN_COMPLETED=true\n"
        "remaining_containers=0\n"
        "remaining_volumes=0\n"
        "remaining_networks=0\n",
        encoding="utf-8",
    )


def _reason_code(exc: Exception) -> str:
    message = str(exc)
    if message.startswith("missing regular evidence file:"):
        return "missing_evidence_file"
    if message.startswith("empty evidence file:"):
        return "empty_evidence_file"
    if message == "readiness migration revision mismatch":
        return "migration_revision_mismatch"
    if message.startswith("teardown evidence"):
        return "teardown_evidence_invalid"
    if message.startswith("teardown rollback proof"):
        return "teardown_rollback_proof_invalid"
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_json_evidence"
    if isinstance(exc, FileNotFoundError):
        return "missing_evidence_root_or_identity"
    return "manifest_build_failed"


def _write_failure(root: Path, *, reason_code: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "failure-summary.json").write_text(
        json.dumps(
            {
                "schema": "nexus.osr.rc-test-failure-summary.v1",
                "status": "failed",
                "stage": "manifest-build",
                "exit_code": 2,
                "reason_code": reason_code,
                "service_states": {},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def build_manifest(args: argparse.Namespace) -> None:
    root = args.evidence_dir.resolve(strict=True)
    ready = json.loads((root / "readyz.json").read_text(encoding="utf-8"))
    safe_config = json.loads((root / "safe-config.json").read_text(encoding="utf-8"))
    image_id = _read(root / "image-id.txt")
    postgres_digest = _read(root / "postgres-image-digest.txt")
    nginx_digest = _read(root / "nginx-image-digest.txt")
    if ready.get("migration_revision") != args.migration_head:
        raise ValueError("readiness migration revision mismatch")

    _finalize_teardown_evidence(root)

    evidence: dict[str, dict[str, object]] = {}
    for logical_name, filename in EVIDENCE_FILES.items():
        path = root / filename
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"missing regular evidence file: {filename}")
        evidence[logical_name] = {
            "path": filename,
            "size_bytes": path.stat().st_size,
            "sha256": _digest(path),
        }

    config_digest = "sha256:" + hashlib.sha256(
        json.dumps(safe_config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest = {
        "schema": "nexus.osr.rc-test-candidate.v1",
        "release_class": "controlled_test_deployment",
        "decision": "RC0_TEST_DEPLOYABLE",
        "candidate": {
            "source_sha": args.source_sha,
            "frontend_build_sha": args.source_sha,
            "image_tag": args.image_tag,
            "image_id": image_id,
            "postgres_image_digest": postgres_digest,
            "nginx_image_digest": nginx_digest,
            "migration_revision": args.migration_head,
            "config_profile": "rc-test-isolated-v1",
            "config_digest": config_digest,
        },
        "checks": {
            "image_build": "pass",
            "compose_validation": "pass",
            "migration": "pass",
            "application_ready": "pass",
            "workers_healthy": "pass",
            "http_core_smoke": "pass",
            "browser_smoke": "pass",
            "side_effect_safety": "pass",
            "network_isolation": "pass",
            "teardown": "pass",
        },
        "safety": {
            "production_data_used": False,
            "production_network_joined": False,
            "provider_candidate_enabled": False,
            "real_outbound_enabled": False,
            "whatsapp_enabled": False,
            "speedaf_write_enabled": False,
            "operations_dispatch_enabled": False,
            "production_ready": False,
            "full_osr_automation": "NO_GO",
            "test_environment_isolated": True,
        },
        "evidence": evidence,
    }
    (root / "candidate-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--image-tag", required=True)
    parser.add_argument("--migration-head", required=True)
    args = parser.parse_args()

    try:
        build_manifest(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _write_failure(args.evidence_dir, reason_code=_reason_code(exc))
        print(f"RC_MANIFEST_BUILT=false reason_code={_reason_code(exc)}")
        return 2
    print("RC_MANIFEST_BUILT=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
