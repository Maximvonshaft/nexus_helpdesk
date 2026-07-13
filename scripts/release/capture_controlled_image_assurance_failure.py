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
_SAFE_NAME = re.compile(r"^[a-z0-9_-]{1,80}$")
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
_SIGNAL_KEYS = {
    "present",
    "status",
    "counts",
    "unresolved_count",
    "applied_exception_count",
    "unused_exception_count",
    "critical_count",
    "high_count",
    "unresolved_license_count",
    "finding_count",
    "scanned_files",
    "suppressed_count",
}
_SIGNAL_NAMES = {
    "sbom",
    "policy_inputs",
    "vulnerabilities",
    "licenses",
    "manifest",
    "compliance",
    "binding",
    "structured",
    "artifact_scan",
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
    return value if _SAFE_NAME.fullmatch(value) else None


def _bounded_int(value: object, *, maximum: int = 1_000_000) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= maximum else None


def _counts(payload: dict[str, Any] | None, keys: tuple[str, ...]) -> dict[str, int]:
    if payload is None or not isinstance(payload.get("counts"), dict):
        return {}
    raw = payload["counts"]
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


def _missing(path: Path) -> bool:
    return not path.is_file() or path.is_symlink()


def _failed(payload: dict[str, Any] | None) -> bool:
    status = _status(payload)
    return status is not None and status != "pass"


def _infer_stage(root: Path, payloads: dict[str, dict[str, Any] | None]) -> str:
    if _missing(root / "image.preliminary.cdx.json"):
        return "sbom-preliminary"
    if _missing(root / "image.safe.cdx.json"):
        return "sbom-finalization"
    if _missing(root / "policy-input-validation.json") or _failed(payloads["policy_inputs"]):
        return "policy-input-validation"
    if _missing(root / "installed-license-evidence.json"):
        return "installed-license-evidence"
    if _missing(root / "vulnerability-summary.json") or _failed(payloads["vulnerabilities"]):
        return "vulnerability-policy"
    if _missing(root / "license-summary.json") or _failed(payloads["licenses"]):
        return "license-policy"
    if _missing(root / "release-image-manifest.json") or _failed(payloads["manifest"]):
        return "release-image-manifest"
    if _missing(root / "license-compliance-evidence.json") or _failed(payloads["compliance"]):
        return "license-compliance"
    if _missing(root / "release-image-compliance-binding.json") or _failed(payloads["binding"]):
        return "compliance-binding"
    if _missing(root / "raw-evidence-digests.json"):
        return "raw-evidence-digests"
    if _missing(root / "structured-evidence-scan.json") or _failed(payloads["structured"]):
        return "structured-evidence-validation"
    if _missing(root / "artifact-scan.json") or _failed(payloads["artifact_scan"]):
        return "artifact-scan"
    return "post-assurance-unknown"


def build_summary(*, release_image_dir: Path, source_sha: str, exit_code: int) -> dict[str, Any]:
    source = source_sha.strip().lower()
    if not _SHA40.fullmatch(source):
        raise FailureEvidenceError("source_sha_invalid")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or not 1 <= exit_code <= 255:
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
    summary = {
        "schema": SCHEMA,
        "status": "failed",
        "stage": _infer_stage(release_image_dir, payloads),
        "exit_code": exit_code,
        "source_sha": source,
        "image_pushed": False,
        "deployment_performed": False,
        "signals": {
            "sbom": _signal(payloads["sbom"]),
            "policy_inputs": _signal(payloads["policy_inputs"]),
            "vulnerabilities": _signal(payloads["vulnerabilities"], count_keys=("CRITICAL", "HIGH")),
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


def _validate_signal(name: str, signal: object) -> None:
    if not isinstance(signal, dict) or set(signal) - _SIGNAL_KEYS:
        raise FailureEvidenceError(f"summary_signal_invalid:{name}")
    present = signal.get("present")
    if not isinstance(present, bool):
        raise FailureEvidenceError(f"summary_signal_present_invalid:{name}")
    if not present and signal != {"present": False}:
        raise FailureEvidenceError(f"summary_signal_absent_fields:{name}")
    if "status" in signal and not _SAFE_NAME.fullmatch(str(signal["status"])):
        raise FailureEvidenceError(f"summary_signal_status_invalid:{name}")
    counts = signal.get("counts")
    if counts is not None:
        if not isinstance(counts, dict) or len(counts) > 10:
            raise FailureEvidenceError(f"summary_signal_counts_invalid:{name}")
        for key, value in counts.items():
            if not _SAFE_NAME.fullmatch(str(key)) or _bounded_int(value) is None:
                raise FailureEvidenceError(f"summary_signal_count_invalid:{name}")
    for key in _SIGNAL_KEYS - {"present", "status", "counts"}:
        if key in signal and _bounded_int(signal[key]) is None:
            raise FailureEvidenceError(f"summary_signal_integer_invalid:{name}")


def validate_summary(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise FailureEvidenceError("summary_object_required")
    if set(payload) != {
        "schema",
        "status",
        "stage",
        "exit_code",
        "source_sha",
        "image_pushed",
        "deployment_performed",
        "signals",
    }:
        raise FailureEvidenceError("summary_fields_invalid")
    if payload.get("schema") != SCHEMA or payload.get("status") != "failed":
        raise FailureEvidenceError("summary_identity_invalid")
    stage = str(payload.get("stage") or "")
    if not _SAFE_NAME.fullmatch(stage) or stage not in _ALLOWED_STAGES:
        raise FailureEvidenceError("summary_stage_invalid")
    exit_code = payload.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or not 1 <= exit_code <= 255:
        raise FailureEvidenceError("summary_exit_code_invalid")
    if not _SHA40.fullmatch(str(payload.get("source_sha") or "")):
        raise FailureEvidenceError("summary_source_invalid")
    if payload.get("image_pushed") is not False or payload.get("deployment_performed") is not False:
        raise FailureEvidenceError("summary_safety_invalid")
    signals = payload.get("signals")
    if not isinstance(signals, dict) or set(signals) != _SIGNAL_NAMES:
        raise FailureEvidenceError("summary_signals_invalid")
    for name, signal in signals.items():
        _validate_signal(name, signal)
    if len(json.dumps(payload, sort_keys=True).encode("utf-8")) > MAX_OUTPUT_BYTES:
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
            if any(value is None for value in (args.release_image_dir, args.source_sha, args.exit_code, args.output)):
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
