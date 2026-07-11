from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("build_recovery_evidence.py")
SPEC = importlib.util.spec_from_file_location("nexus_recovery_evidence", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RecoveryEvidenceTests(unittest.TestCase):
    def _write(self, root: Path, name: str, payload) -> Path:
        path = root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _snapshot(self, *, head: str = "20260711_0058", markets: int = 1, marker: int = 1):
        return {
            "schema_version": "nexus_recovery_snapshot_v1",
            "alembic_head": head,
            "table_count": 3,
            "tables": {"markets": markets, "teams": 1, "service_heartbeats": 0},
            "invalid_foreign_key_count": 0,
            "synthetic_marker_count": marker,
        }

    def _compare(self, root: Path, source_payload, restored_payload, **overrides):
        source = self._write(root, "source.json", source_payload)
        restored = self._write(root, "restored.json", restored_payload)
        output = root / "evidence.json"
        values = {
            "source_sha": "a" * 40,
            "backup_sha256": "sha256:" + "b" * 64,
            "marker_committed_at": "2026-07-12T00:00:00+00:00",
            "backup_completed_at": "2026-07-12T00:00:05+00:00",
            "restore_started_at": "2026-07-12T00:00:06+00:00",
            "restore_completed_at": "2026-07-12T00:00:16+00:00",
            "rto_target_seconds": 120,
            "rpo_target_seconds": 60,
        }
        values.update(overrides)
        code = MODULE.compare(source, restored, output, **values)
        return code, json.loads(output.read_text(encoding="utf-8"))

    def test_matching_restore_passes_with_bounded_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code, evidence = self._compare(root, self._snapshot(), self._snapshot())
            self.assertEqual(code, 0)
            self.assertEqual(evidence["status"], "pass")
            self.assertEqual(evidence["rto_observed_seconds"], 10.0)
            self.assertEqual(evidence["rpo_observed_seconds"], 5.0)
            self.assertFalse(evidence["production_data_used"])
            self.assertFalse(evidence["production_mutation_performed"])

    def test_head_or_row_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code, evidence = self._compare(
                root,
                self._snapshot(),
                self._snapshot(head="wrong", markets=2),
            )
            self.assertEqual(code, 1)
            self.assertIn("recovery.alembic_head_mismatch", evidence["reasons"])
            self.assertIn("recovery.table_count_mismatch", evidence["reasons"])

    def test_missing_marker_or_invalid_fk_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            restored = self._snapshot(marker=0)
            restored["invalid_foreign_key_count"] = 1
            code, evidence = self._compare(root, self._snapshot(), restored)
            self.assertEqual(code, 1)
            self.assertIn("recovery.synthetic_marker_missing", evidence["reasons"])
            self.assertIn("recovery.foreign_key_not_validated", evidence["reasons"])

    def test_rto_and_rpo_thresholds_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code, evidence = self._compare(
                root,
                self._snapshot(),
                self._snapshot(),
                backup_completed_at="2026-07-12T00:02:00+00:00",
                restore_completed_at="2026-07-12T00:03:00+00:00",
                rto_target_seconds=30,
                rpo_target_seconds=30,
            )
            self.assertEqual(code, 1)
            self.assertIn("recovery.rto_exceeded", evidence["reasons"])
            self.assertIn("recovery.rpo_exceeded", evidence["reasons"])

    def test_invalid_backup_digest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(MODULE.RecoveryEvidenceError, "backup_sha"):
                self._compare(
                    root,
                    self._snapshot(),
                    self._snapshot(),
                    backup_sha256="not-a-digest",
                )


if __name__ == "__main__":
    unittest.main()
