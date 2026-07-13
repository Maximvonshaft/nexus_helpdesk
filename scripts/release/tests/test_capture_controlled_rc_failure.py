from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "capture_controlled_rc_failure.py"
SPEC = importlib.util.spec_from_file_location("capture_controlled_rc_failure", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ControlledRCFailureEvidenceTests(unittest.TestCase):
    def test_capture_uses_last_stage_and_bounded_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "run.log"
            log.write_text("RC_STAGE=bootstrap\nnoise\nRC_STAGE=browser-smoke\n", encoding="utf-8")
            output = MODULE.capture_failure(log_path=log, evidence_dir=root / "evidence", exit_code=17)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], MODULE.SCHEMA)
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["stage"], "browser-smoke")
            self.assertEqual(payload["exit_code"], 17)
            self.assertEqual(payload["reason_code"], "candidate_chain_failed")
            self.assertEqual(payload["service_states"], {})
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            self.assertEqual(MODULE.validate_file(output), payload)

    def test_capture_preserves_valid_existing_details(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "evidence"
            evidence.mkdir()
            existing = {
                "schema": MODULE.SCHEMA,
                "status": "failed",
                "stage": "start-runtime",
                "exit_code": 2,
                "reason_code": "candidate_chain_failed",
                "service_states": {"app-rc": "unhealthy"},
                "diagnostic_hex": "41",
            }
            (evidence / "failure-summary.json").write_text(json.dumps(existing), encoding="utf-8")
            log = root / "run.log"
            log.write_text("no stage marker\n", encoding="utf-8")
            output = MODULE.capture_failure(log_path=log, evidence_dir=evidence, exit_code=2)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["stage"], "start-runtime")
            self.assertEqual(payload["service_states"], {"app-rc": "unhealthy"})
            self.assertEqual(payload["diagnostic_hex"], "41")

    def test_capture_preserves_scanner_stage_rules_and_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "evidence"
            evidence.mkdir()
            existing = {
                "schema": MODULE.SCHEMA,
                "status": "failed",
                "stage": "artifact-scan",
                "exit_code": 1,
                "reason_code": "artifact_scan_findings",
                "service_states": {},
                "finding_rules": ["artifact:tracking"],
                "finding_paths": ["artifacts/rc-test/network-safety.json"],
            }
            (evidence / "failure-summary.json").write_text(json.dumps(existing), encoding="utf-8")
            log = root / "run.log"
            log.write_text("RC_STAGE=completed\n", encoding="utf-8")

            output = MODULE.capture_failure(log_path=log, evidence_dir=evidence, exit_code=1)
            payload = json.loads(output.read_text(encoding="utf-8"))

            self.assertEqual(payload["stage"], "artifact-scan")
            self.assertEqual(payload["reason_code"], "artifact_scan_findings")
            self.assertEqual(payload["finding_rules"], ["artifact:tracking"])
            self.assertEqual(
                payload["finding_paths"],
                ["artifacts/rc-test/network-safety.json"],
            )
            self.assertEqual(MODULE.validate_file(output), payload)

    def test_validate_rejects_unknown_fields(self) -> None:
        payload = {
            "schema": MODULE.SCHEMA,
            "status": "failed",
            "stage": "unknown",
            "exit_code": 1,
            "reason_code": "candidate_chain_failed",
            "service_states": {},
            "raw_log": "forbidden",
        }
        with self.assertRaisesRegex(MODULE.FailureEvidenceError, "summary_fields_invalid"):
            MODULE.validate_summary(payload)

    def test_validate_rejects_finding_path_outside_rc_root(self) -> None:
        payload = {
            "schema": MODULE.SCHEMA,
            "status": "failed",
            "stage": "artifact-scan",
            "exit_code": 1,
            "reason_code": "artifact_scan_findings",
            "service_states": {},
            "finding_paths": ["other/raw-log.txt"],
        }
        with self.assertRaisesRegex(MODULE.FailureEvidenceError, "summary_finding_paths_invalid"):
            MODULE.validate_summary(payload)

    def test_validate_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_text("{}", encoding="utf-8")
            link = root / "summary.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(MODULE.FailureEvidenceError, "summary_file_invalid"):
                MODULE.validate_file(link)


if __name__ == "__main__":
    unittest.main()
