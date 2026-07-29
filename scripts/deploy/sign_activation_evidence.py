#!/usr/bin/env python3
"""Sign one reviewed Nexus activation-evidence Manifest with Ed25519.

The unsigned input must already contain the exact candidate, configuration,
environment and evidence receipts. This offline command owns the private key;
application and preflight runtimes receive only the corresponding public key.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_SCHEMA = "nexus.activation-evidence.v3"
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


def _private_key(key_bytes: bytes) -> Ed25519PrivateKey:
    try:
        loaded = serialization.load_pem_private_key(key_bytes, password=None)
    except (TypeError, ValueError) as exc:
        raise SignActivationEvidenceError("private_key_invalid") from exc
    if not isinstance(loaded, Ed25519PrivateKey):
        raise SignActivationEvidenceError("private_key_algorithm_invalid")
    return loaded


def sign_manifest(
    unsigned: dict[str, Any],
    *,
    private_key: Ed25519PrivateKey,
    key_id: str,
) -> dict[str, Any]:
    normalized_key_id = str(key_id or "").strip()
    if not normalized_key_id or len(normalized_key_id) > 120:
        raise SignActivationEvidenceError("key_id_invalid")
    signature = private_key.sign(_canonical_json(unsigned))
    encoded = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return {
        **unsigned,
        "signature": {
            "algorithm": "ed25519",
            "key_id": normalized_key_id,
            "value": encoded,
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


def run(
    *,
    input_path: Path,
    private_key_path: Path,
    key_id: str,
    output_path: Path,
) -> dict[str, Any]:
    unsigned = _load_unsigned(input_path)
    private_key = _private_key(
        _read_bounded(private_key_path, label="private_key").strip()
    )
    signed = sign_manifest(
        unsigned,
        private_key=private_key,
        key_id=key_id,
    )
    encoded = _canonical_json(signed) + b"\n"
    _atomic_write(output_path, encoded)
    return {
        "schema": "nexus.activation-evidence-signing-receipt.v2",
        "status": "pass",
        "output": str(output_path),
        "manifest_sha256": "sha256:"
        + hashlib.sha256(_canonical_json(signed)).hexdigest(),
        "signature_algorithm": "ed25519",
        "key_id": key_id,
        "contains_secrets": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--private-key-file", type=Path, required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = run(
            input_path=args.input,
            private_key_path=args.private_key_file,
            key_id=args.key_id,
            output_path=args.output,
        )
    except (SignActivationEvidenceError, OSError) as exc:
        print(f"activation_evidence_signing_error:{exc}", file=os.sys.stderr)
        return 2
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
