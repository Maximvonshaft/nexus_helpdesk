#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_PATTERNS = [
    r"OPENCLAW_RESPONSES_URL",
    r"OPENCLAW_RESPONSES_TOKEN",
    r"openclaw_gateway_token",
    r"openclaw-gateway",
    r"/v1/responses",
    r"Bearer\s+[A-Za-z0-9_\-.]{12,}",
]

TEXT_EXTENSIONS = {
    ".html", ".js", ".mjs", ".cjs", ".css", ".json", ".map", ".txt", ".md", ".svg"
}


def _iter_files(paths: list[Path]):
    for root in paths:
        if not root.exists():
            continue
        if root.is_file():
            yield root
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in TEXT_EXTENSIONS:
                yield path


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan browser/static bundles for OpenClaw URLs or tokens")
    parser.add_argument("--dist", action="append", default=[])
    parser.add_argument("--static", action="append", default=[])
    parser.add_argument("--pattern", action="append", default=[])
    args = parser.parse_args()

    roots = [Path(p) for p in args.dist + args.static]
    patterns = [re.compile(p, re.IGNORECASE) for p in DEFAULT_PATTERNS + args.pattern]
    findings = []
    for file_path in _iter_files(roots):
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in patterns:
            for match in pattern.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                findings.append({
                    "file": str(file_path),
                    "line": line_no,
                    "pattern": pattern.pattern,
                    "match_preview": match.group(0)[:80],
                })
    report = {"scanned_roots": [str(p) for p in roots], "findings": findings, "finding_count": len(findings)}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
