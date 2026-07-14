#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence

SCHEMA = "nexus.provider-runtime.hygiene.v1"
PRODUCTION_ROOTS = ("backend/app", "backend/scripts", "deploy", "webapp/src", "scripts")
GENERIC_RETIRED_MARKERS = (
    ("retired_codex_app_server", "codex_app_server"),
    ("retired_codex_direct", "codex_direct"),
    ("retired_webchat_fast_reply", "webchat_fast_reply"),
    ("retired_canned_tracking_reply", "Please provide your tracking number"),
)
OPENAI_RESPONSES_MARKER = "openai_responses"
OPENAI_RESPONSE_PROBE_PATH = "scripts/ai/probe_ai_resource_server.py"
OPENAI_RESPONSE_PROBE_ALLOWED_LINES = (
    re.compile(r'^\s*TEST_OPENAI_RESPONSES\s*=\s*["\']openai_responses["\']\s*$'),
    re.compile(r'^\s*def\s+_test_openai_responses_api\s*\('),
    re.compile(r'^\s*TEST_OPENAI_RESPONSES\s*:\s*_test_openai_responses_api\s*,?\s*$'),
)


def _is_allowed_openai_response_probe_line(path: str, line: str) -> bool:
    return path == OPENAI_RESPONSE_PROBE_PATH and any(
        pattern.search(line) for pattern in OPENAI_RESPONSE_PROBE_ALLOWED_LINES
    )


def scan_text(path: str, text: str) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for code, marker in GENERIC_RETIRED_MARKERS:
            if marker in line:
                findings.append({"code": code, "path": path, "line": line_number})
        if OPENAI_RESPONSES_MARKER in line and not _is_allowed_openai_response_probe_line(path, line):
            findings.append(
                {
                    "code": "retired_openai_responses_provider_identifier",
                    "path": path,
                    "line": line_number,
                }
            )
    return findings


def _tracked_paths(repo_root: Path, roots: Sequence[str]) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z", "--", *roots],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return sorted(
        path
        for path in completed.stdout.decode("utf-8", errors="strict").split("\0")
        if path
    )


def scan_repository(
    repo_root: Path,
    *,
    roots: Sequence[str] = PRODUCTION_ROOTS,
) -> dict[str, object]:
    root = repo_root.resolve()
    findings: list[dict[str, object]] = []
    for relative in _tracked_paths(root, roots):
        path = root / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        findings.extend(scan_text(relative, text))
    findings.sort(key=lambda row: (str(row["path"]), int(row["line"]), str(row["code"])))
    return {
        "schema": SCHEMA,
        "status": "pass" if not findings else "fail",
        "finding_count": len(findings),
        "findings": findings[:100],
        "truncated": len(findings) > 100,
    }


def write_result(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit retired Provider Runtime identifiers in tracked executable paths."
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payload = scan_repository(args.repo_root)
        write_result(args.output, payload)
    except (OSError, UnicodeError, subprocess.SubprocessError, ValueError) as exc:
        payload = {
            "schema": SCHEMA,
            "status": "error",
            "finding_count": 0,
            "findings": [],
            "truncated": False,
            "reason_code": "hygiene_checker_error",
        }
        write_result(args.output, payload)
        print(
            "PROVIDER_RUNTIME_HYGIENE=false "
            f"reason_code=hygiene_checker_error error_type={type(exc).__name__}"
        )
        return 2
    if payload["status"] != "pass":
        print(f"PROVIDER_RUNTIME_HYGIENE=false finding_count={payload['finding_count']}")
        return 1
    print("PROVIDER_RUNTIME_HYGIENE=true finding_count=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
