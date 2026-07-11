#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from scanner import bounded_report, scan_artifact_files, write_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", required=True)
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    findings = scan_artifact_files(root, args.paths)
    write_report(
        Path(args.output),
        bounded_report(schema="nexus_security_artifact_scan_v1", findings=findings, scanned_files=len(args.paths)),
    )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
