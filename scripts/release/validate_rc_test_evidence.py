#!/usr/bin/env python3
"""Validate the exact bounded RC artifact set before secret/PII scanning."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

MAX_BYTES = 512 * 1024
SUCCESS_REQUIRED = {
    "source-sha.txt",
    "image-tag.txt",
    "image-id.txt",
    "postgres-image-digest.txt",
    "nginx-image-digest.txt",
    "compose-services.txt",
    "compose-images.txt",
    "safe-config.json",
    "migration-head.txt",
    "migration-current.txt",
    "migration.txt",
    "seed-first.txt",
    "seed-second.txt",
    "seed-verification.json",
    "compose-ps-healthy.txt",
    "healthz.json",
    "readyz.json",
    "http-core-smoke.json",
    "side-effect-safety.json",
    "network-safety.json",
    "browser-smoke.txt",
    "teardown.txt",
    "rollback-verification.json",
    "candidate-manifest.json",
}
FAILURE_ALLOWED = SUCCESS_REQUIRED | {
    "compose-ps-failure.txt",
    "bounded-failure-logs.txt",
    "failure-summary.json",
}
POST_SCAN_ALLOWED = FAILURE_ALLOWED | {
    "artifact-scan.json",
}
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


class EvidenceSetError(ValueError):
    def __init__(self, reason_code: str, entries: list[str] | None = None) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.entries = [name for name in (entries or []) if SAFE_NAME_RE.fullmatch(name)][:10]


def _write_failure(root: Path, exc: EvidenceSetError) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "schema": "nexus.osr.rc-test-failure-summary.v1",
        "status": "failed",
        "stage": "artifact-scan",
        "exit_code": 2,
        "reason_code": exc.reason_code,
        "service_states": {},
    }
    if exc.entries:
        payload["evidence_entries"] = exc.entries
    (root / "failure-summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def validate(root_arg: Path, list_output: Path) -> int:
    repository_root = Path.cwd().resolve()
    try:
        root = root_arg.resolve(strict=True)
    except OSError as exc:
        raise EvidenceSetError("evidence_root_missing") from exc
    try:
        root.relative_to(repository_root)
    except ValueError as exc:
        raise EvidenceSetError("evidence_root_outside_repository") from exc
    if not root.is_dir():
        raise EvidenceSetError("evidence_root_not_directory")

    entries = sorted(root.iterdir(), key=lambda path: path.name)
    if not entries:
        raise EvidenceSetError("evidence_root_empty")
    for entry in entries:
        if entry.is_symlink() or not entry.is_file():
            raise EvidenceSetError("evidence_entry_not_regular", [entry.name])
        if entry.stat().st_size > MAX_BYTES:
            raise EvidenceSetError("evidence_file_too_large", [entry.name])

    names = {entry.name for entry in entries}
    unexpected = sorted(names - POST_SCAN_ALLOWED)
    if unexpected:
        raise EvidenceSetError("unexpected_evidence_files", unexpected)

    success = "candidate-manifest.json" in names
    if success:
        missing = sorted(SUCCESS_REQUIRED - names)
        if missing:
            raise EvidenceSetError("missing_success_evidence_files", missing)
    else:
        diagnostics = {
            "compose-ps-failure.txt",
            "bounded-failure-logs.txt",
            "failure-summary.json",
            "teardown.txt",
        }
        missing = sorted(diagnostics - names)
        if missing:
            raise EvidenceSetError("missing_failure_diagnostics", missing)

    scan_inputs = [
        entry.resolve().relative_to(repository_root)
        for entry in entries
        if entry.name != "artifact-scan.json"
    ]
    list_output.parent.mkdir(parents=True, exist_ok=True)
    list_output.write_text(
        "".join(path.as_posix() + "\n" for path in scan_inputs),
        encoding="utf-8",
    )
    print(f"RC_EVIDENCE_SET_VALID=true mode={'success' if success else 'failure'} files={len(scan_inputs)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--list-output", type=Path, required=True)
    args = parser.parse_args()

    try:
        return validate(args.root, args.list_output)
    except EvidenceSetError as exc:
        root = args.root if args.root.is_absolute() else Path.cwd() / args.root
        _write_failure(root, exc)
        print(f"RC_EVIDENCE_SET_VALID=false reason_code={exc.reason_code}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
