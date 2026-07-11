from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator, Sequence

from scanner import MAX_FILE_BYTES, Finding, load_allowlist, scan_secret_text, write_report


SCHEMA = "nexus_security_git_history_scan_v1"
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_STORED_FINDINGS = 100
MAX_OBJECTS = 2_000_000
MAX_PATH_LENGTH = 320

KNOWN_BINARY_SUFFIXES = frozenset(
    {
        ".7z",
        ".avi",
        ".bin",
        ".bmp",
        ".class",
        ".db",
        ".dll",
        ".dmg",
        ".doc",
        ".docx",
        ".eot",
        ".exe",
        ".gif",
        ".gz",
        ".ico",
        ".jar",
        ".jpeg",
        ".jpg",
        ".lockb",
        ".mov",
        ".mp3",
        ".mp4",
        ".o",
        ".otf",
        ".pdf",
        ".png",
        ".pyc",
        ".sqlite",
        ".sqlite3",
        ".tar",
        ".tgz",
        ".ttf",
        ".wav",
        ".webm",
        ".webp",
        ".woff",
        ".woff2",
        ".xls",
        ".xlsx",
        ".xz",
        ".zip",
    }
)


class HistoryScanError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ObjectMetadata:
    object_id: str
    object_type: str
    size: int
    path: str


@dataclass(frozen=True)
class HistoryFinding:
    rule: str
    path: str
    line: int
    fingerprint: str
    blob_sha: str

    @property
    def allowlist_key(self) -> tuple[str, str, str]:
        return self.path, self.rule, self.fingerprint

    @property
    def identity(self) -> tuple[str, str, int, str, str]:
        return self.path, self.rule, self.line, self.fingerprint, self.blob_sha

    def as_dict(self) -> dict[str, object]:
        return {
            "rule": self.rule,
            "path": self.path,
            "line": self.line,
            "fingerprint": self.fingerprint,
            "blob_sha": self.blob_sha,
        }


def _run_git(root: Path, args: Sequence[str], *, input_bytes: bytes | None = None) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise HistoryScanError("git_command_unavailable") from exc
    if completed.returncode != 0:
        raise HistoryScanError("git_command_failed")
    return completed.stdout


def _validate_sha(value: str, reason: str) -> str:
    if not SHA1_RE.fullmatch(value):
        raise HistoryScanError(reason)
    return value


def _bounded_path(raw: bytes) -> str:
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HistoryScanError("git_object_path_encoding_invalid") from exc
    value = value.strip()
    if not value:
        return "unresolved-blob"
    if value.startswith("/") or "\\" in value or ".." in PurePosixPath(value).parts:
        raise HistoryScanError("git_object_path_invalid")
    if any(character in value for character in "\x00\r\n"):
        raise HistoryScanError("git_object_path_invalid")
    if len(value) > MAX_PATH_LENGTH:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
        suffix = PurePosixPath(value).suffix[:24]
        return f"long-path/{digest}{suffix}"
    return value


def parse_object_listing(raw: bytes) -> dict[str, str]:
    """Parse `git rev-list --objects --all` into object-id to deterministic path."""
    paths: dict[str, str] = {}
    for line in raw.splitlines():
        if not line:
            continue
        object_bytes, separator, path_bytes = line.partition(b" ")
        try:
            object_id = object_bytes.decode("ascii")
        except UnicodeDecodeError as exc:
            raise HistoryScanError("git_object_listing_invalid") from exc
        _validate_sha(object_id, "git_object_listing_invalid")
        path = _bounded_path(path_bytes) if separator else "unresolved-blob"
        existing = paths.get(object_id)
        if existing is None or path < existing:
            paths[object_id] = path
        if len(paths) > MAX_OBJECTS:
            raise HistoryScanError("git_object_limit_exceeded")
    if not paths:
        raise HistoryScanError("git_object_listing_empty")
    return paths


def resolve_object_metadata(
    root: Path,
    object_paths: dict[str, str],
) -> tuple[ObjectMetadata, ...]:
    object_ids = tuple(sorted(object_paths))
    request = "".join(f"{object_id}\n" for object_id in object_ids).encode("ascii")
    raw = _run_git(
        root,
        ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        input_bytes=request,
    )
    lines = raw.splitlines()
    if len(lines) != len(object_ids):
        raise HistoryScanError("git_object_metadata_count_mismatch")

    records: list[ObjectMetadata] = []
    for expected, line in zip(object_ids, lines, strict=True):
        parts = line.decode("ascii", errors="strict").split(" ")
        if len(parts) != 3:
            raise HistoryScanError("git_object_metadata_invalid")
        object_id, object_type, raw_size = parts
        _validate_sha(object_id, "git_object_metadata_invalid")
        if object_id != expected or object_type not in {"blob", "commit", "tag", "tree"}:
            raise HistoryScanError("git_object_metadata_invalid")
        try:
            size = int(raw_size)
        except ValueError as exc:
            raise HistoryScanError("git_object_metadata_invalid") from exc
        if size < 0:
            raise HistoryScanError("git_object_metadata_invalid")
        records.append(
            ObjectMetadata(
                object_id=object_id,
                object_type=object_type,
                size=size,
                path=object_paths[object_id],
            )
        )
    return tuple(records)


def iter_blob_contents(
    root: Path,
    blobs: Sequence[ObjectMetadata],
) -> Iterator[tuple[ObjectMetadata, bytes]]:
    try:
        process = subprocess.Popen(
            ["git", "-C", str(root), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise HistoryScanError("git_batch_unavailable") from exc
    if process.stdin is None or process.stdout is None:
        process.kill()
        raise HistoryScanError("git_batch_pipe_unavailable")

    try:
        for blob in blobs:
            process.stdin.write(blob.object_id.encode("ascii") + b"\n")
            process.stdin.flush()
            header = process.stdout.readline()
            try:
                object_id, object_type, raw_size = header.decode("ascii").rstrip("\n").split(" ")
            except (UnicodeDecodeError, ValueError) as exc:
                raise HistoryScanError("git_blob_header_invalid") from exc
            _validate_sha(object_id, "git_blob_header_invalid")
            try:
                size = int(raw_size)
            except ValueError as exc:
                raise HistoryScanError("git_blob_header_invalid") from exc
            if object_id != blob.object_id or object_type != "blob" or size != blob.size:
                raise HistoryScanError("git_blob_header_mismatch")
            data = process.stdout.read(size)
            separator = process.stdout.read(1)
            if len(data) != size or separator != b"\n":
                raise HistoryScanError("git_blob_payload_truncated")
            yield blob, data
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        return_code = process.wait()
        if return_code != 0 and sys.exc_info()[0] is None:
            raise HistoryScanError("git_batch_failed")


def _is_known_binary_path(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in KNOWN_BINARY_SUFFIXES


def _current_source_sha(root: Path) -> str:
    value = _run_git(root, ["rev-parse", "HEAD"]).decode("ascii").strip()
    return _validate_sha(value, "git_source_sha_invalid")


def _commit_count(root: Path) -> int:
    raw = _run_git(root, ["rev-list", "--all", "--count"]).decode("ascii").strip()
    try:
        count = int(raw)
    except ValueError as exc:
        raise HistoryScanError("git_commit_count_invalid") from exc
    if count < 1:
        raise HistoryScanError("git_commit_count_invalid")
    return count


def _refs_digest(root: Path) -> str:
    raw = _run_git(root, ["for-each-ref", "--format=%(refname)%00%(objectname)"])
    if not raw:
        raise HistoryScanError("git_refs_empty")
    return hashlib.sha256(raw).hexdigest()


def _history_finding(blob: ObjectMetadata, finding: Finding) -> HistoryFinding:
    return HistoryFinding(
        rule=finding.rule,
        path=finding.path,
        line=finding.line,
        fingerprint=finding.fingerprint,
        blob_sha=blob.object_id,
    )


def scan_repository_history(
    root: Path,
    *,
    allowlist_path: Path,
    max_blob_bytes: int = MAX_FILE_BYTES,
) -> dict[str, object]:
    repo_root = root.resolve()
    if max_blob_bytes < 1:
        raise ValueError("history_scan_max_blob_bytes_invalid")

    source_sha = _current_source_sha(repo_root)
    commit_count = _commit_count(repo_root)
    refs_sha256 = _refs_digest(repo_root)
    object_paths = parse_object_listing(
        _run_git(repo_root, ["rev-list", "--objects", "--all"])
    )
    metadata = resolve_object_metadata(repo_root, object_paths)
    blobs = tuple(record for record in metadata if record.object_type == "blob")

    eligible: list[ObjectMetadata] = []
    oversized_binary_count = 0
    unscanned_oversized_count = 0
    for blob in blobs:
        if blob.size <= max_blob_bytes:
            eligible.append(blob)
        elif _is_known_binary_path(blob.path):
            oversized_binary_count += 1
        else:
            unscanned_oversized_count += 1

    allowlist = load_allowlist(allowlist_path)
    allowed = {entry.key for entry in allowlist}
    finding_identities: set[tuple[str, str, int, str, str]] = set()
    stored_findings: list[HistoryFinding] = []
    by_rule: Counter[str] = Counter()
    total_findings = 0
    suppressed_count = 0
    scanned_blob_count = 0
    binary_blob_count = 0

    for blob, data in iter_blob_contents(repo_root, tuple(eligible)):
        if b"\x00" in data[:4096]:
            binary_blob_count += 1
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            binary_blob_count += 1
            continue
        scanned_blob_count += 1
        for base_finding in scan_secret_text(blob.path, text):
            finding = _history_finding(blob, base_finding)
            if finding.identity in finding_identities:
                continue
            finding_identities.add(finding.identity)
            if finding.allowlist_key in allowed:
                suppressed_count += 1
                continue
            total_findings += 1
            by_rule[finding.rule] += 1
            if len(stored_findings) < MAX_STORED_FINDINGS:
                stored_findings.append(finding)

    complete = unscanned_oversized_count == 0
    status = "pass" if complete and total_findings == 0 else "fail"
    return {
        "schema_version": SCHEMA,
        "status": status,
        "complete": complete,
        "source_sha": source_sha,
        "refs_sha256": refs_sha256,
        "commit_count": commit_count,
        "reachable_object_count": len(metadata),
        "reachable_blob_count": len(blobs),
        "scanned_blob_count": scanned_blob_count,
        "binary_blob_count": binary_blob_count,
        "oversized_binary_blob_count": oversized_binary_count,
        "unscanned_oversized_blob_count": unscanned_oversized_count,
        "finding_count": total_findings,
        "suppressed_count": suppressed_count,
        "by_rule": dict(sorted(by_rule.items())),
        "findings": [finding.as_dict() for finding in stored_findings],
        "findings_truncated": total_findings > len(stored_findings),
    }


def _failure_report(reason: str) -> dict[str, object]:
    return {
        "schema_version": SCHEMA,
        "status": "fail",
        "complete": False,
        "failure_reason": reason,
        "source_sha": None,
        "refs_sha256": None,
        "commit_count": 0,
        "reachable_object_count": 0,
        "reachable_blob_count": 0,
        "scanned_blob_count": 0,
        "binary_blob_count": 0,
        "oversized_binary_blob_count": 0,
        "unscanned_oversized_blob_count": 0,
        "finding_count": 0,
        "suppressed_count": 0,
        "by_rule": {},
        "findings": [],
        "findings_truncated": False,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan all reachable Git blobs for redacted credential findings."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=Path("config/security/secret-scan-allowlist.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/security-git-history-scan.json"),
    )
    parser.add_argument("--max-blob-bytes", type=int, default=MAX_FILE_BYTES)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = args.root.resolve()
    allowlist_path = args.allowlist
    output_path = args.output
    if not allowlist_path.is_absolute():
        allowlist_path = root / allowlist_path
    if not output_path.is_absolute():
        output_path = root / output_path

    try:
        report = scan_repository_history(
            root,
            allowlist_path=allowlist_path,
            max_blob_bytes=args.max_blob_bytes,
        )
    except (HistoryScanError, ValueError) as exc:
        reason = exc.reason if isinstance(exc, HistoryScanError) else str(exc)
        report = _failure_report(reason[:120])
        write_report(output_path, report)
        print(json.dumps({"status": "fail", "reason": reason[:120]}, sort_keys=True))
        return 2

    write_report(output_path, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "complete": report["complete"],
                "commit_count": report["commit_count"],
                "reachable_blob_count": report["reachable_blob_count"],
                "scanned_blob_count": report["scanned_blob_count"],
                "finding_count": report["finding_count"],
                "suppressed_count": report["suppressed_count"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
