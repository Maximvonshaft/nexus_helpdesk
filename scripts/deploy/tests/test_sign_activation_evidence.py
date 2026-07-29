from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import stat
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "sign_activation_evidence.py"
SPEC = importlib.util.spec_from_file_location("sign_activation_evidence", MODULE_PATH)
assert SPEC and SPEC.loader
signer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(signer)


def _unsigned() -> dict[str, object]:
    return {
        "schema": "nexus.activation-evidence.v2",
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


def test_signer_writes_atomic_private_deterministic_manifest(tmp_path: Path) -> None:
    input_path = tmp_path / "unsigned.json"
    key_path = tmp_path / "signing.key"
    output_path = tmp_path / "manifest.json"
    unsigned = _unsigned()
    key = b"activation-evidence-key-0123456789abcdef"
    input_path.write_text(json.dumps(unsigned), encoding="utf-8")
    key_path.write_bytes(key)

    receipt = signer.run(
        input_path=input_path,
        key_path=key_path,
        output_path=output_path,
    )
    signed = json.loads(output_path.read_text(encoding="utf-8"))
    expected = hmac.new(key, _canonical(unsigned), hashlib.sha256).hexdigest()

    assert signed["signature"] == {
        "algorithm": "hmac-sha256",
        "value": expected,
    }
    assert receipt["status"] == "pass"
    assert receipt["contains_secrets"] is False
    assert receipt["manifest_sha256"] == (
        "sha256:" + hashlib.sha256(_canonical(signed)).hexdigest()
    )
    assert stat.S_IMODE(output_path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".manifest.json.*.tmp"))


def test_signer_rejects_existing_signature_and_short_key(tmp_path: Path) -> None:
    input_path = tmp_path / "unsigned.json"
    key_path = tmp_path / "signing.key"
    output_path = tmp_path / "manifest.json"
    already_signed = {
        **_unsigned(),
        "signature": {"algorithm": "hmac-sha256", "value": "0" * 64},
    }
    input_path.write_text(json.dumps(already_signed), encoding="utf-8")
    key_path.write_text("short", encoding="utf-8")

    with pytest.raises(
        signer.SignActivationEvidenceError,
        match="manifest_already_signed",
    ):
        signer.run(
            input_path=input_path,
            key_path=key_path,
            output_path=output_path,
        )

    input_path.write_text(json.dumps(_unsigned()), encoding="utf-8")
    with pytest.raises(
        signer.SignActivationEvidenceError,
        match="signing_key_too_short",
    ):
        signer.run(
            input_path=input_path,
            key_path=key_path,
            output_path=output_path,
        )
    assert not output_path.exists()
