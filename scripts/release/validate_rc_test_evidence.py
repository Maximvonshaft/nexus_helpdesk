#!/usr/bin/env python3
"""Validate the exact bounded RC artifact set before secret/PII scanning."""

from __future__ import annotations

import argparse
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
}
POST_SCAN_ALLOWED = FAILURE_ALLOWED | {
    "artifact-scan.json",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--list-output", type=Path, required=True)
    args = parser.parse_args()

    root = args.root.resolve(strict=True)
    if not root.is_dir():
        raise SystemExit("RC evidence root must be a directory")

    entries = sorted(root.iterdir(), key=lambda path: path.name)
    if not entries:
        raise SystemExit("RC evidence root is empty")
    for entry in entries:
        if entry.is_symlink() or not entry.is_file():
            raise SystemExit(f"RC evidence entry is not a regular file: {entry.name}")
        if entry.stat().st_size > MAX_BYTES:
            raise SystemExit(f"RC evidence file exceeds 512 KiB: {entry.name}")

    names = {entry.name for entry in entries}
    unexpected = sorted(names - POST_SCAN_ALLOWED)
    if unexpected:
        raise SystemExit("unexpected RC evidence files: " + ", ".join(unexpected))

    success = "candidate-manifest.json" in names
    if success:
        missing = sorted(SUCCESS_REQUIRED - names)
        if missing:
            raise SystemExit("missing successful RC evidence files: " + ", ".join(missing))
    else:
        diagnostics = {"compose-ps-failure.txt", "bounded-failure-logs.txt", "teardown.txt"}
        missing = sorted(diagnostics - names)
        if missing:
            raise SystemExit("missing failure diagnostics: " + ", ".join(missing))

    scan_inputs = [entry for entry in entries if entry.name != "artifact-scan.json"]
    args.list_output.parent.mkdir(parents=True, exist_ok=True)
    args.list_output.write_text(
        "".join(str(path) + "\n" for path in scan_inputs),
        encoding="utf-8",
    )
    print(f"RC_EVIDENCE_SET_VALID=true mode={'success' if success else 'failure'} files={len(scan_inputs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
