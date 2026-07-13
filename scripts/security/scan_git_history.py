#!/usr/bin/env python3
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

import scanner
from scanner import MAX_FILE_BYTES, Finding, load_allowlist, write_report


SCHEMA = "nexus_security_git_history_scan_v1"
MAX_STORED_FINDINGS = 100
MAX_STORED_OVERSIZED = 20
MAX_OBJECTS = 2_000_000
MAX_BLOB_PATH_PAIRS = 2_000_000
MAX_PATH_LENGTH = 240
_OBJECT_FORMATS = {"sha1": 40, "sha256": 64}
_SAFE_REASON_RE = re.compile(r"^[a-z0-9_]{3,120}$")


class HistoryScanError(RuntimeError):
    def __init__(self, reason: str):
        safe_reason = reason if _SAFE_REASON_RE.fullmatch(reason) else "history_scan_error"
        super().__init__(safe_reason)
        self.reason = safe_reason


@dataclass(frozen=True)
class ObjectMetadata:
    object_id: str
    object_type: str
    size: int
    representative_path: str


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
    def logical_identity(self) -> tuple[str, str, int, str]:
        return self.path, self.rule, self.line, self.fingerprint

    def as_dict(self) -> dict[str, object]:
        return {
            "rule": self.rule,
            **_path_evidence(self.path),
            "line": self.line,
            "fingerprint": self.fingerprint,
            "blob_sha": self.blob_sha,
        }


def _path_evidence(path: str) -> dict[str, str]:
    return {
        "path_sha256": hashlib.sha256(path.encode("utf-8", errors="replace")).hexdigest(),
        "path_suffix": PurePosixPath(path).suffix.lower()[:24],
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


def _object_format(root: Path) -> tuple[str, int]:
    try:
        value = _run_git(root, ["rev-parse", "--show-object-format"]).decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise HistoryScanError("git_object_format_invalid") from exc
    length = _OBJECT_FORMATS.get(value)
    if length is None:
        raise HistoryScanError("git_object_format_unsupported")
    return value, length


def _validate_object_id(value: str, *, object_id_length: int, reason: str) -> str:
    if len(value) != object_id_length or re.fullmatch(r"[0-9a-f]+", value) is None:
        raise HistoryScanError(reason)
    return value


def _ensure_complete_repository(root: Path) -> None:
    try:
        shallow = _run_git(root, ["rev-parse", "--is-shallow-repository"]).decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise HistoryScanError("git_shallow_state_invalid") from exc
    if shallow == "true":
        raise HistoryScanError("git_repository_shallow")
    if shallow != "false":
        raise HistoryScanError("git_shallow_state_invalid")


def _bounded_path(raw: bytes) -> str:
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HistoryScanError("git_object_path_encoding_invalid") from exc
    if value == "":
        return "unresolved-blob"
    if value.startswith("/") or "\\" in value or ".." in PurePosixPath(value).parts:
        raise HistoryScanError("git_object_path_invalid")
    if any(character in value for character in "\x00\r\n"):
        raise HistoryScanError("git_object_path_invalid")
    if len(value) > MAX_PATH_LENGTH:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
        suffix = PurePosixPath(value).suffix.lower()[:24]
        return f"long-path/{digest}{suffix}"
    return value


def parse_object_listing(raw: bytes, *, object_id_length: int) -> dict[str, str]:
    """Return one bounded fallback path per reachable object.

    `rev-list --objects` is used for the complete object set. Complete Blob path
    aliases are collected separately from every reachable root Tree.
    """

    paths: dict[str, str] = {}
    for line in raw.splitlines():
        if not line:
            continue
        object_bytes, separator, path_bytes = line.partition(b" ")
        try:
            object_id = object_bytes.decode("ascii")
        except UnicodeDecodeError as exc:
            raise HistoryScanError("git_object_listing_invalid") from exc
        _validate_object_id(
            object_id,
            object_id_length=object_id_length,
            reason="git_object_listing_invalid",
        )
        path = _bounded_path(path_bytes) if separator else "unresolved-blob"
        previous = paths.get(object_id)
        if previous is None or path < previous:
            paths[object_id] = path
        if len(paths) > MAX_OBJECTS:
            raise HistoryScanError("git_object_limit_exceeded")
    if not paths:
        raise HistoryScanError("git_object_listing_empty")
    return paths


def resolve_object_metadata(
    root: Path,
    object_paths: dict[str, str],
    *,
    object_id_length: int,
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
        try:
            parts = line.decode("ascii").split(" ")
        except UnicodeDecodeError as exc:
            raise HistoryScanError("git_object_metadata_invalid") from exc
        if len(parts) != 3:
            raise HistoryScanError("git_object_metadata_invalid")
        object_id, object_type, raw_size = parts
        _validate_object_id(
            object_id,
            object_id_length=object_id_length,
            reason="git_object_metadata_invalid",
        )
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
                representative_path=object_paths[object_id],
            )
        )
    return tuple(records)


def _reference_object_ids(root: Path, *, object_id_length: int) -> tuple[str, ...]:
    raw = _run_git(
        root,
        [
            "for-each-ref",
            "--format=%(objectname)",
            "refs/heads",
            "refs/remotes",
            "refs/tags",
        ],
    )
    object_ids: set[str] = set()
    for line in raw.splitlines():
        if not line:
            continue
        try:
            value = line.decode("ascii")
        except UnicodeDecodeError as exc:
            raise HistoryScanError("git_refs_invalid") from exc
        object_ids.add(
            _validate_object_id(
                value,
                object_id_length=object_id_length,
                reason="git_refs_invalid",
            )
        )
    if not object_ids:
        raise HistoryScanError("git_refs_empty")
    return tuple(sorted(object_ids))


def _try_peel_tree(
    root: Path,
    object_id: str,
    *,
    object_id_length: int,
) -> str | None:
    _validate_object_id(
        object_id,
        object_id_length=object_id_length,
        reason="git_ref_object_invalid",
    )
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", f"{object_id}^{{tree}}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError as exc:
        raise HistoryScanError("git_command_unavailable") from exc
    if completed.returncode != 0:
        return None
    try:
        value = completed.stdout.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise HistoryScanError("git_ref_tree_invalid") from exc
    return _validate_object_id(
        value,
        object_id_length=object_id_length,
        reason="git_ref_tree_invalid",
    )


def _root_tree_ids(root: Path, *, object_id_length: int) -> tuple[str, ...]:
    raw = _run_git(root, ["log", "--all", "--format=%T"])
    trees: set[str] = set()
    for line in raw.splitlines():
        if not line:
            continue
        try:
            value = line.decode("ascii")
        except UnicodeDecodeError as exc:
            raise HistoryScanError("git_root_tree_invalid") from exc
        trees.add(
            _validate_object_id(
                value,
                object_id_length=object_id_length,
                reason="git_root_tree_invalid",
            )
        )

    # Branches and tags may directly reference a Tree rather than a commit.
    # Peel every allowed reference object to a Tree when possible.
    for object_id in _reference_object_ids(root, object_id_length=object_id_length):
        tree_id = _try_peel_tree(
            root,
            object_id,
            object_id_length=object_id_length,
        )
        if tree_id is not None:
            trees.add(tree_id)

    if not trees:
        raise HistoryScanError("git_root_tree_empty")
    return tuple(sorted(trees))


def _parse_ls_tree(
    raw: bytes,
    *,
    object_id_length: int,
) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        if not separator:
            raise HistoryScanError("git_tree_listing_invalid")
        parts = metadata.split(b" ")
        if len(parts) != 3:
            raise HistoryScanError("git_tree_listing_invalid")
        _mode, raw_type, raw_object_id = parts
        try:
            object_type = raw_type.decode("ascii")
            object_id = raw_object_id.decode("ascii")
        except UnicodeDecodeError as exc:
            raise HistoryScanError("git_tree_listing_invalid") from exc
        _validate_object_id(
            object_id,
            object_id_length=object_id_length,
            reason="git_tree_listing_invalid",
        )
        if object_type == "commit":
            continue
        if object_type != "blob":
            raise HistoryScanError("git_tree_listing_invalid")
        pairs.append((object_id, _bounded_path(raw_path)))
    return tuple(pairs)


def collect_blob_paths(
    root: Path,
    blobs: Sequence[ObjectMetadata],
    *,
    object_id_length: int,
) -> dict[str, tuple[str, ...]]:
    blob_ids = {blob.object_id for blob in blobs}
    aliases: dict[str, set[str]] = {blob.object_id: set() for blob in blobs}
    pair_count = 0
    for tree_id in _root_tree_ids(root, object_id_length=object_id_length):
        raw = _run_git(root, ["ls-tree", "-r", "-z", "--full-tree", tree_id])
        for object_id, path in _parse_ls_tree(raw, object_id_length=object_id_length):
            if object_id not in blob_ids:
                raise HistoryScanError("git_tree_blob_unreachable")
            paths = aliases[object_id]
            if path not in paths:
                paths.add(path)
                pair_count += 1
                if pair_count > MAX_BLOB_PATH_PAIRS:
                    raise HistoryScanError("git_blob_path_limit_exceeded")

    for blob in blobs:
        if not aliases[blob.object_id]:
            aliases[blob.object_id].add(blob.representative_path)
            pair_count += 1
            if pair_count > MAX_BLOB_PATH_PAIRS:
                raise HistoryScanError("git_blob_path_limit_exceeded")

    return {
        object_id: tuple(sorted(paths))
        for object_id, paths in sorted(aliases.items())
    }


def iter_blob_contents(
    root: Path,
    blobs: Sequence[ObjectMetadata],
    *,
    object_id_length: int,
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
        process.wait()
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
            _validate_object_id(
                object_id,
                object_id_length=object_id_length,
                reason="git_blob_header_invalid",
            )
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
    except BaseException:
        process.kill()
        process.wait()
        raise
    else:
        process.stdin.close()
        if process.wait() != 0:
            raise HistoryScanError("git_batch_failed")


def _iter_secret_findings(relative_path: str, text: str) -> Iterator[Finding]:
    for line_no, line in enumerate(text.splitlines(), start=1):
        if scanner._is_placeholder(line):
            continue
        for rule, pattern in scanner._PATTERNS:
            for match in pattern.finditer(line):
                yield Finding(
                    rule,
                    relative_path,
                    line_no,
                    scanner._fingerprint(rule, relative_path, line_no, match.group(0)),
                )


def _current_source_sha(root: Path, *, object_id_length: int) -> str:
    try:
        value = _run_git(root, ["rev-parse", "HEAD"]).decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise HistoryScanError("git_source_sha_invalid") from exc
    return _validate_object_id(
        value,
        object_id_length=object_id_length,
        reason="git_source_sha_invalid",
    )


def _commit_count(root: Path) -> int:
    try:
        raw = _run_git(root, ["rev-list", "--all", "--count"]).decode("ascii").strip()
        count = int(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise HistoryScanError("git_commit_count_invalid") from exc
    if count < 1:
        raise HistoryScanError("git_commit_count_invalid")
    return count


def _refs_digest(root: Path, *, object_id_length: int) -> str:
    object_ids = _reference_object_ids(root, object_id_length=object_id_length)
    return hashlib.sha256(("\n".join(object_ids) + "\n").encode("ascii")).hexdigest()


def _history_finding(blob: ObjectMetadata, finding: Finding) -> HistoryFinding:
    return HistoryFinding(
        rule=finding.rule,
        path=finding.path,
        line=finding.line,
        fingerprint=finding.fingerprint,
        blob_sha=blob.object_id,
    )


def failure_report(reason: str, *, object_id_length: int) -> dict[str, object]:
    safe_reason = reason if _SAFE_REASON_RE.fullmatch(reason) else "history_scan_error"
    return {
        "schema_version": SCHEMA,
        "status": "fail",
        "complete": False,
        "failure_reason": safe_reason,
        "object_id_length": object_id_length if object_id_length in {40, 64} else 0,
        "source_sha": None,
        "refs_sha256": None,
        "commit_count": 0,
        "reachable_object_count": 0,
        "reachable_blob_count": 0,
        "reachable_blob_path_count": 0,
        "accounted_blob_count": 0,
        "scanned_text_blob_count": 0,
        "binary_blob_count": 0,
        "oversized_binary_blob_count": 0,
        "unscanned_oversized_blob_count": 0,
        "unscanned_oversized": [],
        "unscanned_oversized_truncated": False,
        "finding_count": 0,
        "suppressed_count": 0,
        "by_rule": [],
        "findings": [],
        "findings_truncated": False,
    }


def scan_repository_history(
    root: Path,
    *,
    allowlist_path: Path,
    max_blob_bytes: int = MAX_FILE_BYTES,
) -> dict[str, object]:
    repo_root = root.resolve()
    if max_blob_bytes < 1:
        raise ValueError("history_scan_max_blob_bytes_invalid")

    _ensure_complete_repository(repo_root)
    _object_name, object_id_length = _object_format(repo_root)
    source_sha = _current_source_sha(repo_root, object_id_length=object_id_length)
    commit_count = _commit_count(repo_root)
    refs_sha256 = _refs_digest(repo_root, object_id_length=object_id_length)
    object_paths = parse_object_listing(
        _run_git(repo_root, ["rev-list", "--objects", "--all"]),
        object_id_length=object_id_length,
    )
    metadata = resolve_object_metadata(
        repo_root,
        object_paths,
        object_id_length=object_id_length,
    )
    blobs = tuple(record for record in metadata if record.object_type == "blob")
    blob_paths = collect_blob_paths(
        repo_root,
        blobs,
        object_id_length=object_id_length,
    )

    eligible: list[ObjectMetadata] = []
    unscanned_oversized: list[ObjectMetadata] = []
    for blob in blobs:
        if blob.size <= max_blob_bytes:
            eligible.append(blob)
        else:
            # A suffix is not evidence of binary content. Oversized Blobs remain
            # incomplete until the configured ceiling is raised and bytes are
            # actually inspected.
            unscanned_oversized.append(blob)

    allowlist = load_allowlist(allowlist_path)
    allowed = {entry.key for entry in allowlist}
    logical_findings: set[tuple[str, str, int, str]] = set()
    stored_findings: list[HistoryFinding] = []
    by_rule: Counter[str] = Counter()
    total_findings = 0
    suppressed_count = 0
    scanned_text_count = 0
    binary_count = 0

    for blob, data in iter_blob_contents(
        repo_root,
        tuple(eligible),
        object_id_length=object_id_length,
    ):
        if b"\x00" in data[:4096]:
            binary_count += 1
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            binary_count += 1
            continue
        scanned_text_count += 1
        for path in blob_paths[blob.object_id]:
            for base_finding in _iter_secret_findings(path, text):
                finding = _history_finding(blob, base_finding)
                if finding.logical_identity in logical_findings:
                    continue
                logical_findings.add(finding.logical_identity)
                if finding.allowlist_key in allowed:
                    suppressed_count += 1
                    continue
                total_findings += 1
                by_rule[finding.rule] += 1
                if len(stored_findings) < MAX_STORED_FINDINGS:
                    stored_findings.append(finding)

    accounted_blob_count = scanned_text_count + binary_count + len(unscanned_oversized)
    complete = accounted_blob_count == len(blobs) and not unscanned_oversized
    status = "pass" if complete and total_findings == 0 else "fail"
    oversized_records = []
    for blob in unscanned_oversized[:MAX_STORED_OVERSIZED]:
        paths = blob_paths[blob.object_id]
        representative_path = paths[0] if paths else blob.representative_path
        oversized_records.append(
            {
                **_path_evidence(representative_path),
                "path_count": len(paths),
                "size_bytes": blob.size,
                "blob_sha": blob.object_id,
            }
        )

    report = {
        "schema_version": SCHEMA,
        "status": status,
        "complete": complete,
        "failure_reason": None,
        "object_id_length": object_id_length,
        "source_sha": source_sha,
        "refs_sha256": refs_sha256,
        "commit_count": commit_count,
        "reachable_object_count": len(metadata),
        "reachable_blob_count": len(blobs),
        "reachable_blob_path_count": sum(len(paths) for paths in blob_paths.values()),
        "accounted_blob_count": accounted_blob_count,
        "scanned_text_blob_count": scanned_text_count,
        "binary_blob_count": binary_count,
        "oversized_binary_blob_count": 0,
        "unscanned_oversized_blob_count": len(unscanned_oversized),
        "unscanned_oversized": oversized_records,
        "unscanned_oversized_truncated": len(unscanned_oversized) > len(oversized_records),
        "finding_count": total_findings,
        "suppressed_count": suppressed_count,
        "by_rule": [
            {"rule": rule, "count": count}
            for rule, count in sorted(by_rule.items())
        ],
        "findings": [finding.as_dict() for finding in stored_findings],
        "findings_truncated": total_findings > len(stored_findings),
    }
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise HistoryScanError("history_report_too_large")
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan all reachable Git Blobs for redacted credential findings."
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
    allowlist_path = args.allowlist if args.allowlist.is_absolute() else root / args.allowlist
    output_path = args.output if args.output.is_absolute() else root / args.output
    try:
        _format, object_id_length = _object_format(root)
    except HistoryScanError:
        object_id_length = 0

    try:
        report = scan_repository_history(
            root,
            allowlist_path=allowlist_path,
            max_blob_bytes=args.max_blob_bytes,
        )
    except (HistoryScanError, ValueError) as exc:
        reason = exc.reason if isinstance(exc, HistoryScanError) else str(exc)
        report = failure_report(reason, object_id_length=object_id_length)
        write_report(output_path, report)
        print(json.dumps({"status": "fail", "reason": report["failure_reason"]}, sort_keys=True))
        return 2

    write_report(output_path, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "complete": report["complete"],
                "commit_count": report["commit_count"],
                "reachable_blob_count": report["reachable_blob_count"],
                "reachable_blob_path_count": report["reachable_blob_path_count"],
                "accounted_blob_count": report["accounted_blob_count"],
                "finding_count": report["finding_count"],
                "suppressed_count": report["suppressed_count"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
