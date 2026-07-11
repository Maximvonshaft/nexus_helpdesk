#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from scanner import (
    apply_allowlist,
    bounded_report,
    load_allowlist,
    scan_secret_files,
    write_report,
)


def _tracked_files(root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [item.decode("utf-8") for item in completed.stdout.split(b"\0") if item]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--allowlist",
        default="config/security/secret-scan-allowlist.json",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    files = _tracked_files(root)
    findings = scan_secret_files(root, files)
    allowlist = load_allowlist(root / args.allowlist)
    findings, suppressed_count = apply_allowlist(findings, allowlist)
    write_report(
        Path(args.output),
        bounded_report(
            schema="nexus_security_secret_scan_v1",
            findings=findings,
            scanned_files=len(files),
            suppressed_count=suppressed_count,
        ),
    )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
