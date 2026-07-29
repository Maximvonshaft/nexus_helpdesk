#!/usr/bin/env python3
"""Sign one reviewed Nexus activation-evidence Manifest.

The unsigned input must already contain the exact candidate, configuration,
environment and evidence receipts. This command adds only the HMAC-SHA256
signature, writes atomically, and never prints key material or evidence content.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import tempfile
from pathlib import Path
from typing import Any

_SCHEMA = "nexus.activation-evidence.v2"
_MAX_BYTES = 1024 * 1024


class SignActivationEvidenceError(ValueError):
    pass


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _read_bounded(path: Path, *, label: str) -> bytes:
    try:
        with path.open("rb") as handle:
            payload = handle.read(_MAX_BYTES + 1)
    except OSError as exc:
        raise SignActivationEvidenceError(f"{label}_unavailable") from exc
    if not payload or len(payload) > _MAX_BYTES:
        raise SignActivationEvidenceError(f"{label}_size_invalid")
    return payload


def _load_unsigned(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(_read_bounded(path, label="manifest").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SignActivationEvidenceError("manifest_invalid") from exc
    if not isinstance(payload, dict):
        raise SignActivationEvidenceError("manifest_not_object")
    if payload.get("schema") != _SCHEMA:
        raise SignActivationEvidenceError("manifest_schema_invalid")
    if "signature" in payload:
        raise SignActivationEvidenceError("manifest_already_signed")
    candidate = payload.get("candidate")
    evidence = payload.get("evidence")
    if not isinstance(candidate, dict) or not candidate:
        raise SignActivationEvidenceError("manifest_candidate_missing")
    if not isinstance(evidence, dict) or not evidence:
        raise SignActivationEvidenceError("manifest_evidence_missing")
    return payload


def sign_manifest(unsigned: dict[str, Any], *, key: bytes) -> dict[str, Any]:
    if len(key) < 32:
        raise SignActivationEvidenceError("signing_key_too_short")
    signature = hmac.new(key, _canonical_json(unsigned), hashlib.sha256).hexdigest()
    return {
        **unsigned,
        "signature": {
            "algorithm": "hmac-sha256",
            "value": signature,
        },
    }


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def run(*, input_path: Path, key_path: Path, output_path: Path) -> dict[str, Any]:
    unsigned = _load_unsigned(input_path)
    key = _read_bounded(key_path, label="signing_key").strip()
    signed = sign_manifest(unsigned, key=key)
    encoded = _canonical_json(signed) + b"\n"
    _atomic_write(output_path, encoded)
    return {
        "schema": "nexus.activation-evidence-signing-receipt.v1",
        "status": "pass",
        "output": str(output_path),
        "manifest_sha256": "sha256:" + hashlib.sha256(_canonical_json(signed)).hexdigest(),
        "signature_algorithm": "hmac-sha256",
        "contains_secrets": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = run(
            input_path=args.input,
            key_path=args.key_file,
            output_path=args.output,
        )
    except (SignActivationEvidenceError, OSError) as exc:
        print(f"activation_evidence_signing_error:{exc}", file=os.sys.stderr)
        return 2
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
