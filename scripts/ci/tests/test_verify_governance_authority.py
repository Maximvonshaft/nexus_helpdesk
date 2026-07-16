from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "scripts" / "verify_governance_authority.py"
SPEC = importlib.util.spec_from_file_location("verify_governance_authority", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


VALID_PROTOCOL = b"""protocol_id: nexus-governance-15-lane-v3.1
protocol_version: 3.1.0
status: CURRENT
authority:
  branch: governance/audit-control-plane
  issue_ledger: 722
permissions:
  only_automatic_write_surface: Issue 722 append-only comments
safety:
  default_posture: NO_GO
"""


class GovernanceAuthorityVerifierTests(unittest.TestCase):
    def _write_fixture(self, directory: Path, **overrides: object) -> tuple[Path, Path]:
        protocol_path = directory / "protocol.yaml"
        protocol_path.write_bytes(VALID_PROTOCOL)
        pointer = {
            "schema": "nexus.governance.authority-ref.v1",
            "status": "CURRENT",
            "repository": "Maximvonshaft/nexus_helpdesk",
            "branch": "governance/audit-control-plane",
            "commit": "f" * 40,
            "protocol_id": "nexus-governance-15-lane-v3.1",
            "protocol_version": "3.1.0",
            "protocol_path": "audit-control-plane/protocol/nexus-audit-controller-v3.1.yaml",
            "protocol_digest_sha256": hashlib.sha256(VALID_PROTOCOL).hexdigest(),
            "issue_ledger": 722,
        }
        pointer.update(overrides)
        pointer_path = directory / "pointer.json"
        pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
        return pointer_path, protocol_path

    def test_valid_exact_pointer_and_offline_protocol_pass(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            pointer, protocol = self._write_fixture(Path(raw))
            result = MODULE.verify(pointer, protocol, timeout=0.1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "offline_file")
        self.assertEqual(result["governance_commit"], "f" * 40)

    def test_digest_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            pointer, protocol = self._write_fixture(Path(raw), protocol_digest_sha256="0" * 64)
            with self.assertRaises(MODULE.VerificationError) as caught:
                MODULE.verify(pointer, protocol, timeout=0.1)
        self.assertEqual(caught.exception.code, "protocol_digest_mismatch")

    def test_mutable_or_malformed_commit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            pointer, protocol = self._write_fixture(Path(raw), commit="governance/audit-control-plane")
            with self.assertRaises(MODULE.VerificationError) as caught:
                MODULE.verify(pointer, protocol, timeout=0.1)
        self.assertEqual(caught.exception.code, "commit_invalid")

    def test_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            pointer, protocol = self._write_fixture(Path(raw), protocol_path="audit-control-plane/protocol/../../main.py")
            with self.assertRaises(MODULE.VerificationError) as caught:
                MODULE.verify(pointer, protocol, timeout=0.1)
        self.assertEqual(caught.exception.code, "protocol_path_invalid")

    def test_wrong_branch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            pointer, protocol = self._write_fixture(Path(raw), branch="main")
            with self.assertRaises(MODULE.VerificationError) as caught:
                MODULE.verify(pointer, protocol, timeout=0.1)
        self.assertEqual(caught.exception.code, "branch_mismatch")

    def test_protocol_identity_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            pointer, protocol = self._write_fixture(directory)
            bad_protocol = VALID_PROTOCOL.replace(b"protocol_version: 3.1.0", b"protocol_version: 3.2.0")
            protocol.write_bytes(bad_protocol)
            pointer_data = json.loads(pointer.read_text(encoding="utf-8"))
            pointer_data["protocol_digest_sha256"] = hashlib.sha256(bad_protocol).hexdigest()
            pointer.write_text(json.dumps(pointer_data), encoding="utf-8")
            with self.assertRaises(MODULE.VerificationError) as caught:
                MODULE.verify(pointer, protocol, timeout=0.1)
        self.assertEqual(caught.exception.code, "protocol_identity_mismatch")


if __name__ == "__main__":
    unittest.main()
