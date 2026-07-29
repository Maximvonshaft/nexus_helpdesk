from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import stat
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

MODULE_PATH = Path(__file__).resolve().parents[1] / "sign_activation_evidence.py"
SPEC = importlib.util.spec_from_file_location("sign_activation_evidence", MODULE_PATH)
assert SPEC and SPEC.loader
signer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(signer)


def _unsigned() -> dict[str, object]:
    return {
        "schema": "nexus.activation-evidence.v3",
        "candidate": {
            "source_sha": "a" * 40,
            "image_digest": "sha256:" + "b" * 64,
            "configuration_digest": "sha256:" + "c" * 64,
            "environment_id": "production-eu-1",
        },
        "evidence": {
            "production_e2e_evidence_url": {
                "url": "https://evidence.example/production",
                "result": "pass",
                "artifact_sha256": "sha256:" + "d" * 64,
                "source_sha": "a" * 40,
                "image_digest": "sha256:" + "b" * 64,
                "configuration_digest": "sha256:" + "c" * 64,
                "environment_id": "production-eu-1",
                "generated_at": "2026-07-29T00:00:00+00:00",
            }
        },
    }


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _private_key_file(path: Path) -> Ed25519PrivateKey:
    private_key = Ed25519PrivateKey.generate()
    path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return private_key


def test_signer_writes_atomic_private_deterministic_manifest(tmp_path: Path) -> None:
    input_path = tmp_path / "unsigned.json"
    key_path = tmp_path / "signing-private.pem"
    output_path = tmp_path / "manifest.json"
    unsigned = _unsigned()
    private_key = _private_key_file(key_path)
    input_path.write_text(json.dumps(unsigned), encoding="utf-8")

    receipt = signer.run(
        input_path=input_path,
        private_key_path=key_path,
        key_id="activation-test-2026-01",
        output_path=output_path,
    )
    signed = json.loads(output_path.read_text(encoding="utf-8"))
    signature_bytes = base64.urlsafe_b64decode(
        signed["signature"]["value"] + "=="
    )
    private_key.public_key().verify(signature_bytes, _canonical(unsigned))

    assert signed["signature"]["algorithm"] == "ed25519"
    assert signed["signature"]["key_id"] == "activation-test-2026-01"
    assert receipt["status"] == "pass"
    assert receipt["signature_algorithm"] == "ed25519"
    assert receipt["contains_secrets"] is False
    assert receipt["manifest_sha256"] == (
        "sha256:" + hashlib.sha256(_canonical(signed)).hexdigest()
    )
    assert stat.S_IMODE(output_path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".manifest.json.*.tmp"))


def test_signer_rejects_existing_signature_and_non_ed25519_key(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "unsigned.json"
    key_path = tmp_path / "signing-private.pem"
    output_path = tmp_path / "manifest.json"
    already_signed = {
        **_unsigned(),
        "signature": {
            "algorithm": "ed25519",
            "key_id": "existing-key",
            "value": "A" * 86,
        },
    }
    input_path.write_text(json.dumps(already_signed), encoding="utf-8")
    _private_key_file(key_path)

    with pytest.raises(
        signer.SignActivationEvidenceError,
        match="manifest_already_signed",
    ):
        signer.run(
            input_path=input_path,
            private_key_path=key_path,
            key_id="activation-test-2026-01",
            output_path=output_path,
        )

    from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key

    rsa_key = generate_private_key(public_exponent=65537, key_size=2048)
    key_path.write_bytes(
        rsa_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    input_path.write_text(json.dumps(_unsigned()), encoding="utf-8")
    with pytest.raises(
        signer.SignActivationEvidenceError,
        match="private_key_algorithm_invalid",
    ):
        signer.run(
            input_path=input_path,
            private_key_path=key_path,
            key_id="activation-test-2026-01",
            output_path=output_path,
        )
    assert not output_path.exists()
