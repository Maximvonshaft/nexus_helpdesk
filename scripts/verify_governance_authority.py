#!/usr/bin/env python3
"""Verify the immutable Nexus audit-control-plane authority pointer.

The verifier deliberately accepts no mutable branch-only authority. It validates the
pointer, resolves the protocol from the exact governance commit (or an explicitly
provided offline file), verifies SHA-256, and checks bounded protocol identity fields.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import math
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

EXPECTED_REPOSITORY = "Maximvonshaft/nexus_helpdesk"
EXPECTED_BRANCH = "governance/audit-control-plane"
EXPECTED_PROTOCOL_ID = "nexus-governance-15-lane-v3.1"
EXPECTED_PROTOCOL_VERSION = "3.1.0"
EXPECTED_PROTOCOL_PATH = "audit-control-plane/protocol/nexus-audit-controller-v3.1.yaml"
EXPECTED_ORCHESTRATION_PATH = "audit-control-plane/protocol/task-orchestration-v1.yaml"
EXPECTED_MANIFEST_PATH = "audit-control-plane/protocol/protocol-manifest.yaml"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POINTER = REPOSITORY_ROOT / "docs/governance/audit-control-plane.ref.json"
MAX_POINTER_BYTES = 64 * 1024
MAX_PROTOCOL_BYTES = 1024 * 1024
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class VerificationError(RuntimeError):
    """Bounded fail-closed authority verification error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AuthorityPointer:
    repository: str
    branch: str
    commit: str
    protocol_id: str
    protocol_version: str
    protocol_path: str
    orchestration_path: str
    manifest_path: str
    protocol_digest_sha256: str
    issue_ledger: int


def _read_bounded(path: Path, maximum: int, code: str) -> bytes:
    try:
        with path.open("rb") as handle:
            data = handle.read(maximum + 1)
    except OSError as exc:
        raise VerificationError(code, f"cannot read {path}") from exc
    if len(data) > maximum:
        raise VerificationError(code, f"{path} exceeds bounded size")
    return data


def _require_string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise VerificationError("pointer_field_invalid", f"pointer field {key} is invalid")
    return value


def load_pointer(path: Path) -> AuthorityPointer:
    data = _read_bounded(path, MAX_POINTER_BYTES, "pointer_read_failed")
    try:
        raw = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError("pointer_json_invalid", "pointer is not valid UTF-8 JSON") from exc
    if not isinstance(raw, dict):
        raise VerificationError("pointer_json_invalid", "pointer root must be an object")
    if raw.get("schema") != "nexus.governance.authority-ref.v1":
        raise VerificationError("pointer_schema_invalid", "unexpected pointer schema")
    if raw.get("status") != "CURRENT":
        raise VerificationError("pointer_not_current", "pointer status is not CURRENT")

    issue_ledger = raw.get("issue_ledger")
    if issue_ledger != 722:
        raise VerificationError("pointer_issue_invalid", "pointer must bind Issue 722")

    pointer = AuthorityPointer(
        repository=_require_string(raw, "repository"),
        branch=_require_string(raw, "branch"),
        commit=_require_string(raw, "commit"),
        protocol_id=_require_string(raw, "protocol_id"),
        protocol_version=_require_string(raw, "protocol_version"),
        protocol_path=_require_string(raw, "protocol_path"),
        orchestration_path=_require_string(raw, "orchestration_path"),
        manifest_path=_require_string(raw, "manifest_path"),
        protocol_digest_sha256=_require_string(raw, "protocol_digest_sha256"),
        issue_ledger=issue_ledger,
    )
    validate_pointer(pointer)
    return pointer


def validate_pointer(pointer: AuthorityPointer) -> None:
    if pointer.repository != EXPECTED_REPOSITORY:
        raise VerificationError("repository_mismatch", "unexpected governance repository")
    if pointer.branch != EXPECTED_BRANCH:
        raise VerificationError("branch_mismatch", "unexpected governance branch")
    if not HEX40.fullmatch(pointer.commit):
        raise VerificationError("commit_invalid", "governance commit must be 40 lowercase hex characters")
    if pointer.protocol_id != EXPECTED_PROTOCOL_ID:
        raise VerificationError("protocol_id_mismatch", "unexpected protocol id")
    if pointer.protocol_version != EXPECTED_PROTOCOL_VERSION:
        raise VerificationError("protocol_version_mismatch", "unexpected protocol version")
    if not HEX64.fullmatch(pointer.protocol_digest_sha256):
        raise VerificationError("protocol_digest_invalid", "protocol digest must be 64 lowercase hex characters")

    expected_paths = {
        "protocol_path": (pointer.protocol_path, EXPECTED_PROTOCOL_PATH),
        "orchestration_path": (pointer.orchestration_path, EXPECTED_ORCHESTRATION_PATH),
        "manifest_path": (pointer.manifest_path, EXPECTED_MANIFEST_PATH),
    }
    for field, (raw_path, expected_path) in expected_paths.items():
        path = PurePosixPath(raw_path)
        if path.is_absolute() or ".." in path.parts:
            raise VerificationError(f"{field}_invalid", f"{field} escapes governance root")
        if not raw_path.startswith("audit-control-plane/protocol/") or path.suffix not in {".yaml", ".yml"}:
            raise VerificationError(f"{field}_invalid", f"{field} is outside the YAML protocol root")
        if raw_path != expected_path:
            raise VerificationError(f"{field}_mismatch", f"unexpected {field}")


def _validate_timeout(timeout: float) -> None:
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 60:
        raise VerificationError("timeout_invalid", "timeout must be finite and within (0, 60] seconds")


def fetch_protocol(pointer: AuthorityPointer, timeout: float) -> bytes:
    _validate_timeout(timeout)
    encoded_path = "/".join(urllib.parse.quote(part, safe="") for part in PurePosixPath(pointer.protocol_path).parts)
    url = f"https://raw.githubusercontent.com/{pointer.repository}/{pointer.commit}/{encoded_path}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "nexus-governance-authority-verifier/1"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(MAX_PROTOCOL_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException) as exc:
        raise VerificationError("protocol_fetch_failed", "cannot fetch exact governance protocol") from exc
    if len(data) > MAX_PROTOCOL_BYTES:
        raise VerificationError("protocol_too_large", "protocol exceeds bounded size")
    return data


def _extract_scalar(text: str, key: str) -> str | None:
    pattern = re.compile(rf"(?m)^{re.escape(key)}:\s*([^#\r\n]+?)\s*$")
    match = pattern.search(text)
    if not match:
        return None
    value = match.group(1).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value


def verify_protocol(pointer: AuthorityPointer, data: bytes) -> dict[str, Any]:
    digest = hashlib.sha256(data).hexdigest()
    if digest != pointer.protocol_digest_sha256:
        raise VerificationError("protocol_digest_mismatch", "protocol SHA-256 does not match pointer")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VerificationError("protocol_utf8_invalid", "protocol is not valid UTF-8") from exc

    expected = {
        "protocol_id": pointer.protocol_id,
        "protocol_version": pointer.protocol_version,
        "status": "CURRENT",
    }
    observed = {key: _extract_scalar(text, key) for key in expected}
    for key, value in expected.items():
        if observed[key] != value:
            raise VerificationError("protocol_identity_mismatch", f"protocol field {key} does not match pointer")

    required_markers = (
        "branch: governance/audit-control-plane",
        "issue_ledger: 722",
        "only_automatic_write_surface: Issue 722 append-only comments",
        "default_posture: NO_GO",
    )
    for marker in required_markers:
        if marker not in text:
            raise VerificationError("protocol_contract_missing", f"required protocol marker missing: {marker}")

    return {
        "ok": True,
        "repository": pointer.repository,
        "branch": pointer.branch,
        "governance_commit": pointer.commit,
        "protocol_id": pointer.protocol_id,
        "protocol_version": pointer.protocol_version,
        "protocol_path": pointer.protocol_path,
        "orchestration_path": pointer.orchestration_path,
        "manifest_path": pointer.manifest_path,
        "protocol_digest_sha256": digest,
        "issue_ledger": pointer.issue_ledger,
    }


def verify(pointer_path: Path, offline_protocol: Path | None, timeout: float) -> dict[str, Any]:
    _validate_timeout(timeout)
    pointer = load_pointer(pointer_path)
    if offline_protocol is None:
        protocol_data = fetch_protocol(pointer, timeout)
        source = "exact_remote_commit"
    else:
        protocol_data = _read_bounded(offline_protocol, MAX_PROTOCOL_BYTES, "protocol_read_failed")
        source = "offline_file"
    result = verify_protocol(pointer, protocol_data)
    result["source"] = source
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pointer", type=Path, default=DEFAULT_POINTER)
    parser.add_argument("--offline-protocol", type=Path)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--json", action="store_true", help="emit one bounded JSON result")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = verify(args.pointer, args.offline_protocol, args.timeout)
    except VerificationError as exc:
        payload = {"ok": False, "code": exc.code, "message": str(exc)}
        print(json.dumps(payload, sort_keys=True) if args.json else f"FAIL {exc.code}: {exc}")
        return 1
    print(json.dumps(result, sort_keys=True) if args.json else f"PASS {result['protocol_id']}@{result['governance_commit']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
