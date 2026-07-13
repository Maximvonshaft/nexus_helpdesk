#!/usr/bin/env python3
"""Capture and validate bounded RC failure evidence for controlled publication."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

SCHEMA = "nexus.osr.rc-test-failure-summary.v1"
MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_SUMMARY_BYTES = 64 * 1024
MAX_SERVICE_STATES = 64
MAX_FINDING_ITEMS = 10
_STAGE_RE = re.compile(r"^[a-z0-9_-]{1,64}$")
_REASON_RE = re.compile(r"^[a-z0-9_-]{1,80}$")
_SERVICE_RE = re.compile(r"^[a-z0-9_-]{1,80}$")
_STATE_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
_FINDING_RULE_RE = re.compile(r"^[a-z0-9_:.-]{2,80}$")
_FINDING_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]{1,240}$")
_DIAGNOSTIC_HEX_RE = re.compile(r"^[0-9a-f]{2,800}$")
_STAGE_LINE_RE = re.compile(r"(?m)^RC_STAGE=([a-z0-9_-]{1,64})$")


class FailureEvidenceError(ValueError):
    pass


def _read_bounded_text(path: Path, *, max_bytes: int) -> str:
    if not path.is_file() or path.is_symlink():
        raise FailureEvidenceError(f"file_invalid:{path.name}")
    size = path.stat().st_size
    if size > max_bytes:
        with path.open("rb") as handle:
            handle.seek(size - max_bytes)
            raw = handle.read(max_bytes)
    else:
        raw = path.read_bytes()
    return raw.decode("utf-8", errors="replace")


def _load_existing(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_SUMMARY_BYTES:
        raise FailureEvidenceError("existing_summary_invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FailureEvidenceError("existing_summary_json_invalid") from exc
    return payload if isinstance(payload, dict) else {}


def _bounded_service_states(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > MAX_SERVICE_STATES:
        return {}
    result: dict[str, str] = {}
    for raw_key, raw_state in value.items():
        key = str(raw_key)
        state = str(raw_state)
        if _SERVICE_RE.fullmatch(key) and _STATE_RE.fullmatch(state):
            result[key] = state
    return dict(sorted(result.items()))


def _bounded_finding_rules(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_FINDING_ITEMS:
        return []
    result: list[str] = []
    for raw in value:
        item = str(raw)
        if _FINDING_RULE_RE.fullmatch(item) and item not in result:
            result.append(item)
    return sorted(result)


def _bounded_finding_paths(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_FINDING_ITEMS:
        return []
    result: list[str] = []
    for raw in value:
        item = str(raw)
        if (
            item.startswith("artifacts/rc-test/")
            and _FINDING_PATH_RE.fullmatch(item)
            and item not in result
        ):
            result.append(item)
    return sorted(result)


def validate_summary(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise FailureEvidenceError("summary_object_required")
    if payload.get("schema") != SCHEMA:
        raise FailureEvidenceError("summary_schema_invalid")
    if payload.get("status") != "failed":
        raise FailureEvidenceError("summary_status_invalid")

    stage = str(payload.get("stage") or "")
    reason = str(payload.get("reason_code") or "")
    exit_code = payload.get("exit_code")
    states = payload.get("service_states")
    if not _STAGE_RE.fullmatch(stage):
        raise FailureEvidenceError("summary_stage_invalid")
    if not _REASON_RE.fullmatch(reason):
        raise FailureEvidenceError("summary_reason_invalid")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool) or not 1 <= exit_code <= 255:
        raise FailureEvidenceError("summary_exit_code_invalid")
    if not isinstance(states, dict) or states != _bounded_service_states(states):
        raise FailureEvidenceError("summary_service_states_invalid")

    allowed = {
        "schema",
        "status",
        "stage",
        "exit_code",
        "reason_code",
        "service_states",
        "diagnostic_hex",
        "finding_rules",
        "finding_paths",
    }
    if set(payload) - allowed:
        raise FailureEvidenceError("summary_fields_invalid")
    if "diagnostic_hex" in payload:
        diagnostic = str(payload["diagnostic_hex"])
        if not _DIAGNOSTIC_HEX_RE.fullmatch(diagnostic):
            raise FailureEvidenceError("summary_diagnostic_invalid")
    if "finding_rules" in payload:
        rules = payload["finding_rules"]
        if not isinstance(rules, list) or rules != _bounded_finding_rules(rules):
            raise FailureEvidenceError("summary_finding_rules_invalid")
    if "finding_paths" in payload:
        paths = payload["finding_paths"]
        if not isinstance(paths, list) or paths != _bounded_finding_paths(paths):
            raise FailureEvidenceError("summary_finding_paths_invalid")
    return dict(payload)


def capture_failure(*, log_path: Path, evidence_dir: Path, exit_code: int) -> Path:
    if not isinstance(exit_code, int) or isinstance(exit_code, bool) or not 1 <= exit_code <= 255:
        raise FailureEvidenceError("exit_code_invalid")
    log_text = _read_bounded_text(log_path, max_bytes=MAX_LOG_BYTES)
    matches = _STAGE_LINE_RE.findall(log_text.replace("\r\n", "\n"))
    stage = matches[-1] if matches else "unknown"

    evidence_dir.mkdir(parents=True, exist_ok=True)
    if evidence_dir.is_symlink() or not evidence_dir.is_dir():
        raise FailureEvidenceError("evidence_dir_invalid")
    output = evidence_dir / "failure-summary.json"
    existing = _load_existing(output)

    reason = str(existing.get("reason_code") or "")
    if not _REASON_RE.fullmatch(reason):
        reason = "candidate_chain_failed"
    existing_stage = str(existing.get("stage") or "")
    if _STAGE_RE.fullmatch(existing_stage) and (
        stage == "unknown" or reason != "candidate_chain_failed"
    ):
        stage = existing_stage

    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "failed",
        "stage": stage,
        "exit_code": exit_code,
        "reason_code": reason,
        "service_states": _bounded_service_states(existing.get("service_states")),
    }
    diagnostic = str(existing.get("diagnostic_hex") or "")
    if _DIAGNOSTIC_HEX_RE.fullmatch(diagnostic):
        payload["diagnostic_hex"] = diagnostic
    finding_rules = _bounded_finding_rules(existing.get("finding_rules"))
    if finding_rules:
        payload["finding_rules"] = finding_rules
    finding_paths = _bounded_finding_paths(existing.get("finding_paths"))
    if finding_paths:
        payload["finding_paths"] = finding_paths
    validate_summary(payload)

    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if len(encoded.encode("utf-8")) > MAX_SUMMARY_BYTES:
        raise FailureEvidenceError("summary_too_large")
    fd, temporary_name = tempfile.mkstemp(prefix=".failure-summary.", dir=evidence_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary_name).replace(output)
    finally:
        temporary = Path(temporary_name)
        if temporary.exists():
            temporary.unlink()
    output.chmod(0o600)
    return output


def validate_file(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_SUMMARY_BYTES:
        raise FailureEvidenceError("summary_file_invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FailureEvidenceError("summary_json_invalid") from exc
    return validate_summary(payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--capture", action="store_true")
    mode.add_argument("--validate", type=Path)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--exit-code", type=int)
    args = parser.parse_args()
    try:
        if args.capture:
            if args.log is None or args.evidence_dir is None or args.exit_code is None:
                raise FailureEvidenceError("capture_arguments_missing")
            path = capture_failure(log_path=args.log, evidence_dir=args.evidence_dir, exit_code=args.exit_code)
            validate_file(path)
        else:
            if args.validate is None:
                raise FailureEvidenceError("validate_path_missing")
            validate_file(args.validate)
    except (FailureEvidenceError, OSError, ValueError) as exc:
        print(f"controlled_rc_failure_evidence_error:{exc}", file=os.sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
