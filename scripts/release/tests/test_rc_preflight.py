from __future__ import annotations

import hashlib
import unittest

from scripts.release.rc_preflight import (
    FAILURE_SCHEMA,
    SCHEMA,
    bounded_result,
    failure_summary,
    validate_registry_text,
)


class RcPreflightTests(unittest.TestCase):
    def test_registry_contract_accepts_pinned_release_skill(self) -> None:
        validate_registry_text(
            "schema: nexus.osr.remote-skills-registry.v1\n"
            "  - name: test_release_candidate_convergence\n"
            "    auto_upgrade: false\n"
        )

    def test_registry_contract_fails_closed_on_missing_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "registry_schema_invalid"):
            validate_registry_text("schema: wrong\n")
        with self.assertRaisesRegex(ValueError, "registry_release_skill_missing"):
            validate_registry_text("schema: nexus.osr.remote-skills-registry.v1\nauto_upgrade: false\n")
        with self.assertRaisesRegex(ValueError, "registry_auto_upgrade_policy_missing"):
            validate_registry_text("schema: nexus.osr.remote-skills-registry.v1\nname: test_release_candidate_convergence\n")

    def test_failure_result_is_bounded_and_contains_no_raw_output(self) -> None:
        raw = b"secret-marker-customer-payload"
        result = bounded_result(status="fail", stage="release_unit_tests", exit_code=1, output=raw)
        self.assertEqual(result["schema"], SCHEMA)
        self.assertEqual(result["stage"], "release_unit_tests")
        self.assertEqual(result["output_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result["output_bytes"], len(raw))
        self.assertNotIn("output", result)
        self.assertNotIn(raw.decode(), str(result))

    def test_failure_summary_uses_rc_artifact_contract(self) -> None:
        summary = failure_summary(
            stage="rc-preflight",
            exit_code=1,
            reason_code="release_unit_tests_failed",
        )
        self.assertEqual(summary, {
            "schema": FAILURE_SCHEMA,
            "status": "failed",
            "stage": "rc-preflight",
            "exit_code": 1,
            "reason_code": "release_unit_tests_failed",
            "service_states": {},
        })

    def test_bounded_fields_reject_unbounded_tokens(self) -> None:
        with self.assertRaisesRegex(ValueError, "stage_invalid"):
            bounded_result(status="fail", stage="contains spaces", exit_code=1)
        with self.assertRaisesRegex(ValueError, "reason_code_invalid"):
            failure_summary(stage="rc-preflight", exit_code=1, reason_code="raw output")


if __name__ == "__main__":
    unittest.main()
