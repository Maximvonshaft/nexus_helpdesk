#!/usr/bin/env python3
"""Build bounded, secret-free diagnostics for controlled image-assurance failures."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA = "nexus.osr.controlled-image-assurance-failure.v1"
MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_STAGE = re.compile(r"^[a-z0-9_-]{1,80}$")
_STATUS = re.compile(r"^[a-z0-9_-]{1,40}$")
_ALLOWED_STAGES = {
    "sbom-preliminary",
    "sbom-finalization",
    "policy-input-validation",
    "installed-license-evidence",
    "vulnerability-policy",
    "license-policy",
    "release-image-manifest",
    "license-compliance",
    "compliance-binding",
    "raw-evidence-digests",
    "structured-evidence-validation",
    "artifact-scan",
    "post-assurance-unknown",
}


class FailureEvidenceError(ValueError):
    pass


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_INPUT_BYTES:
        raise FailureEvidenceError(f"input_invalid:{path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FailureEvidenceError(f"input_json_invalid:{path.name}") from exc
    if not isinstance(payload, dict):
        raise FailureEvidenceError(f"input_object_required:{path.name}")
    return payload


def _status(payload: dict[str, Any] | None) -> str | None:
    if payload is None:
        return None
    value = str(payload.get("status") or "").strip().lower()
    return value if _STATUS.fullmatch(value) else None


def _bounded_int(value: object, *, maximum: int = 1_000_000) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= maximum else None


def _counts(payload: dict[str, Any] | None, keys: tuple[str, ...]) -> dict[str, int]:
    if payload is None:
        return {}
    raw = payload.get("counts")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, int] = {}
    for key in keys:
        value = _bounded_int(raw.get(key))
        if value is not None:
            result[key.lower()] = value
    return result


def _signal(payload: dict[str, Any] | None, *, count_keys: tuple[str, ...] = ()) -> dict[str, Any]:
    if payload is None:
        return {"present": False}
    result: dict[str, Any] = {"present": True}
    status = _status(payload)
    if status is not None:
        result["status"] = status
    counts = _counts(payload, count_keys)
    if counts:
        result["counts"] = counts
    for key in (
        "unresolved_count",
        "applied_exception_count",
        "unused_exception_count",
        "critical_count",
        "high_count",
        "unresolved_license_count",
        "finding_count",
        "scanned_files",
        "suppressed_count",
    ):
        value = _bounded_int(payload.get(key))
        if value is not None:
            result[key] = value
    return result


def _infer_stage(root: Path, payloads: dict[str, dict[str, Any] | None]) -> str:
    ordered = (
        ("sbom-preliminary", root / "image.preliminary.cdx.json"),
        ("sbom-finalization", root / "image.safe.cdx.json"),
        ("policy-input-validation", root / "policy-input-validation.json"),
        ("installed-license-evidence", root / "installed-license-evidence.json"),
        ("vulnerability-policy", root / "vulnerability-summary.json"),
        ("license-policy", root / "license-summary.json"),
        ("release-image-manifest", root / "release-image-manifest.json"),
        ("license-compliance", root / "license-compliance-evidence.json"),
        ("compliance-binding", root / "release-image-compliance-binding.json"),
        ("raw-evidence-digests", root / "raw-evidence-digests.json"),
        ("structured-evidence-validation", root / "structured-evidence-scan.json"),
        ("artifact-scan", root / "artifact-scan.json"),
    )
    for stage, path in ordered:
        if not path.is_file() or path.is_symlink():
            return stage

    status_order = (
        ("policy-input-validation", payloads["policy_inputs"]),
        ("vulnerability-policy", payloads["vulnerabilities"]),
        ("license-policy", payloads["licenses"]),
        ("release-image-manifest", payloads["manifest"]),
        ("license-compliance", payloads["compliance"]),
        ("compliance-binding", payloads["binding"]),
        ("structured-evidence-validation", payloads["structured"]),
        ("artifact-scan", payloads["artifact_scan"]),
    )
    for stage, payload in status_order:
        status = _status(payload)
        if status is not None and status != "pass":
            return stage
    return "post-assurance-unknown"


def build_summary(*, release_image_dir: Path, source_sha: str, exit_code: int) -> dict[str, Any]:
    source = source_sha.strip().lower()
    if not _SHA40.fullmatch(source):
        raise FailureEvidenceError("source_sha_invalid")
    if isinstance(exit_code, bool) or not 1 <= exit_code <= 255:
        raise FailureEvidenceError("exit_code_invalid")
    if not release_image_dir.is_dir() or release_image_dir.is_symlink():
        raise FailureEvidenceError("release_image_dir_invalid")

    payloads = {
        "sbom": _load_json(release_image_dir / "image.safe.cdx.json.summary.json"),
        "policy_inputs": _load_json(release_image_dir / "policy-input-validation.json"),
        "vulnerabilities": _load_json(release_image_dir / "vulnerability-summary.json"),
        "licenses": _load_json(release_image_dir / "license-summary.json"),
        "manifest": _load_json(release_image_dir / "release-image-manifest.json"),
        "compliance": _load_json(release_image_dir / "license-compliance-evidence.json"),
        "binding": _load_json(release_image_dir / "release-image-compliance-binding.json"),
        "structured": _load_json(release_image_dir / "structured-evidence-scan.json"),
        "artifact_scan": _load_json(release_image_dir / "artifact-scan.json"),
    }
    stage = _infer_stage(release_image_dir, payloads)
    summary = {
        "schema": SCHEMA,
        "status": "failed",
        "stage": stage,
        "exit_code": exit_code,
        "source_sha": source,
        "image_pushed": False,
        "deployment_performed": False,
        "signals": {
            "sbom": _signal(payloads["sbom"]),
            "policy_inputs": _signal(payloads["policy_inputs"]),
            "vulnerabilities": _signal(
                payloads["vulnerabilities"], count_keys=("CRITICAL", "HIGH")
            ),
            "licenses": _signal(
                payloads["licenses"],
                count_keys=("components", "allowed", "review", "denied", "unknown"),
            ),
            "manifest": _signal(payloads["manifest"]),
            "compliance": _signal(payloads["compliance"]),
            "binding": _signal(payloads["binding"]),
            "structured": _signal(payloads["structured"]),
            "artifact_scan": _signal(payloads["artifact_scan"]),
        },
    }
    validate_summary(summary)
    return summary


def validate_summary(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise FailureEvidenceError("summary_object_required")
    expected = {
        "schema",
        "status",
        "stage",
        "exit_code",
        "source_sha",
        "image_pushed",
        "deployment_performed",
        "signals",
    }
    if set(payload) != expected:
        raise FailureEvidenceError("summary_fields_invalid")
    if payload.get("schema") != SCHEMA or payload.get("status") != "failed":
        raise FailureEvidenceError("summary_identity_invalid")
    stage = str(payload.get("stage") or "")
    if not _STAGE.fullmatch(stage) or stage not in _ALLOWED_STAGES:
        raise FailureEvidenceError("summary_stage_invalid")
    exit_code = payload.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or not 1 <= exit_code <= 255:
        raise FailureEvidenceError("summary_exit_code_invalid")
    if not _SHA40.fullmatch(str(payload.get("source_sha") or "")):
        raise FailureEvidenceError("summary_source_invalid")
    if payload.get("image_pushed") is not False or payload.get("deployment_performed") is not False:
        raise FailureEvidenceError("summary_safety_invalid")
    signals = payload.get("signals")
    if not isinstance(signals, dict) or set(signals) != {
        "sbom",
        "policy_inputs",
        "vulnerabilities",
        "licenses",
        "manifest",
        "compliance",
        "binding",
        "structured",
        "artifact_scan",
    }:
        raise FailureEvidenceError("summary_signals_invalid")
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise FailureEvidenceError("summary_too_large")
    return dict(payload)


def write_summary(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise FailureEvidenceError("output_path_invalid")
    path.write_text(encoded, encoding="utf-8")
    path.chmod(0o600)


def load_and_validate(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    if payload is None:
        raise FailureEvidenceError("summary_missing")
    return validate_summary(payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--capture", action="store_true")
    mode.add_argument("--validate", type=Path)
    parser.add_argument("--release-image-dir", type=Path)
    parser.add_argument("--source-sha")
    parser.add_argument("--exit-code", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.capture:
            if None in (args.release_image_dir, args.source_sha, args.exit_code, args.output):
                raise FailureEvidenceError("capture_arguments_missing")
            payload = build_summary(
                release_image_dir=args.release_image_dir,
                source_sha=args.source_sha,
                exit_code=args.exit_code,
            )
            write_summary(args.output, payload)
            load_and_validate(args.output)
        else:
            if args.validate is None:
                raise FailureEvidenceError("validate_path_missing")
            load_and_validate(args.validate)
    except (FailureEvidenceError, OSError, ValueError) as exc:
        print(f"controlled_image_assurance_failure_error:{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
