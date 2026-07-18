#!/usr/bin/env python3
"""Verify a local upload backup and write bounded readiness evidence.

This tool does not copy or delete data. It compares source and backup contents,
rejects symbolic links and writes an atomic marker only when both trees match.
Acceptance inputs and outputs must remain outside the candidate repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "nexus.local-storage-backup.v1"
DEFAULT_MARKER = ".nexus-backup-verified.json"


def _inside_candidate_tree(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return False
    return True


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest(root: Path, *, marker_name: str) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        if path.name == marker_name and path.parent == root:
            continue
        if path.is_symlink():
            raise ValueError(f"symbolic_link_not_allowed:{path.relative_to(root)}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        total_bytes += size
        rows.append({
            "path": relative,
            "size": size,
            "sha256": _file_sha256(path),
        })
    return rows, total_bytes


def _manifest_sha256(rows: list[dict[str, Any]]) -> str:
    encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verify_backup(source: Path, backup: Path, *, marker_name: str = DEFAULT_MARKER) -> dict[str, Any]:
    if source.expanduser().is_symlink() or backup.expanduser().is_symlink():
        raise ValueError("source_or_backup_symlink_not_allowed")
    source = source.expanduser().resolve()
    backup = backup.expanduser().resolve()
    if _inside_candidate_tree(source) or _inside_candidate_tree(backup):
        raise ValueError("source_or_backup_inside_candidate_tree")
    if source == backup:
        raise ValueError("source_and_backup_must_differ")
    if not source.is_dir():
        raise ValueError("source_directory_missing")
    if not backup.is_dir():
        raise ValueError("backup_directory_missing")

    source_rows, source_bytes = _manifest(source, marker_name=marker_name)
    backup_rows, backup_bytes = _manifest(backup, marker_name=marker_name)
    source_manifest = _manifest_sha256(source_rows)
    backup_manifest = _manifest_sha256(backup_rows)
    if source_rows != backup_rows or source_bytes != backup_bytes or source_manifest != backup_manifest:
        raise ValueError("source_backup_manifest_mismatch")

    return {
        "schema": SCHEMA,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "source_matches_backup": True,
        "file_count": len(source_rows),
        "total_bytes": source_bytes,
        "manifest_sha256": source_manifest,
        "contains_file_names": False,
        "contains_file_content": False,
    }


def write_marker(backup: Path, payload: dict[str, Any], *, marker_name: str = DEFAULT_MARKER) -> Path:
    if backup.expanduser().is_symlink():
        raise ValueError("backup_symlink_not_allowed")
    backup = backup.expanduser().resolve()
    if _inside_candidate_tree(backup):
        raise ValueError("backup_inside_candidate_tree")
    target = backup / marker_name
    temporary = backup / f".{marker_name}.{os.getpid()}.tmp"
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--marker-name", default=DEFAULT_MARKER)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    payload = verify_backup(args.source, args.backup, marker_name=args.marker_name)
    marker = write_marker(args.backup, payload, marker_name=args.marker_name)
    result = {**payload, "marker": str(marker)}
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = args.output.expanduser().resolve()
        if _inside_candidate_tree(output):
            raise SystemExit("backup qualification output must remain outside candidate tree")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
