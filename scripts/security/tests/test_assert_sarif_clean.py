from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from scripts.security.assert_sarif_clean import (
    SarifValidationError,
    _load_exceptions,
    _sarif_files,
    evaluate,
)


def _sarif_result(rule_id: str, source_path: str, line: int) -> dict:
    return {
        "ruleId": rule_id,
        "level": "warning",
        "message": {"text": "source content must never be copied"},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": source_path},
                    "region": {"startLine": line, "startColumn": 1},
                }
            }
        ],
    }


class AssertSarifCleanTests(unittest.TestCase):
    def test_clean_sarif_passes_without_copying_source_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            sarif = source / "python.sarif"
            sarif.write_text(json.dumps({"version": "2.1.0", "runs": [{"results": []}]}), encoding="utf-8")
            payload = evaluate([sarif])
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["result_count"], 0)
            self.assertFalse(payload["contains_source_snippets"])

    def test_unapproved_codeql_result_fails_with_bounded_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sarif = Path(tmp) / "javascript.sarif"
            sarif.write_text(
                json.dumps({"version": "2.1.0", "runs": [{"results": [_sarif_result("js/sql-injection", "webapp/src/a.ts", 4)]}]}),
                encoding="utf-8",
            )
            payload = evaluate([sarif])
            self.assertEqual(payload["status"], "fail")
            self.assertEqual(payload["by_rule"], [{"rule_id": "js/sql-injection", "count": 1}])
            self.assertNotIn("source content", json.dumps(payload))

    def test_exact_unexpired_exception_passes_and_is_counted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_file = root / "backend/app/protocol.py"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("# protocol marker\nusedforsecurity=False\n", encoding="utf-8")
            policy = root / "exceptions.json"
            policy.write_text(
                json.dumps(
                    {
                        "schema_version": "nexus_codeql_exception_policy_v1",
                        "exceptions": [
                            {
                                "rule_id": "py/weak-sensitive-data-hashing",
                                "path": "backend/app/protocol.py",
                                "start_line": 2,
                                "owner": "security-engineering",
                                "expires_on": (date.today() + timedelta(days=30)).isoformat(),
                                "reason": "Upstream wire protocol requires this exact compatibility digest.",
                                "required_markers": ["protocol marker", "usedforsecurity=False"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            sarif = root / "python.sarif"
            sarif.write_text(
                json.dumps({"version": "2.1.0", "runs": [{"results": [_sarif_result("py/weak-sensitive-data-hashing", "backend/app/protocol.py", 2)]}]}),
                encoding="utf-8",
            )
            exceptions = _load_exceptions(policy, root=root)
            payload = evaluate([sarif], exceptions=exceptions)
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["result_count"], 0)
            self.assertEqual(payload["approved_exception_count"], 1)

    def test_mismatched_or_unused_exception_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_file = root / "backend/app/protocol.py"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("marker\n", encoding="utf-8")
            policy = root / "exceptions.json"
            policy.write_text(
                json.dumps(
                    {
                        "schema_version": "nexus_codeql_exception_policy_v1",
                        "exceptions": [
                            {
                                "rule_id": "py/weak-sensitive-data-hashing",
                                "path": "backend/app/protocol.py",
                                "start_line": 10,
                                "owner": "security-engineering",
                                "expires_on": (date.today() + timedelta(days=30)).isoformat(),
                                "reason": "Upstream wire protocol requires this exact compatibility digest.",
                                "required_markers": ["marker"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            sarif = root / "python.sarif"
            sarif.write_text(json.dumps({"version": "2.1.0", "runs": [{"results": []}]}), encoding="utf-8")
            payload = evaluate([sarif], exceptions=_load_exceptions(policy, root=root))
            self.assertEqual(payload["status"], "fail")
            self.assertEqual(payload["unused_exception_count"], 1)

    def test_expired_exception_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_file = root / "backend/app/protocol.py"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("marker\n", encoding="utf-8")
            policy = root / "exceptions.json"
            policy.write_text(
                json.dumps(
                    {
                        "schema_version": "nexus_codeql_exception_policy_v1",
                        "exceptions": [
                            {
                                "rule_id": "py/weak-sensitive-data-hashing",
                                "path": "backend/app/protocol.py",
                                "start_line": 1,
                                "owner": "security-engineering",
                                "expires_on": date.today().isoformat(),
                                "reason": "Upstream wire protocol requires this exact compatibility digest.",
                                "required_markers": ["marker"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(SarifValidationError):
                _load_exceptions(policy, root=root)

    def test_missing_sarif_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SarifValidationError):
                _sarif_files(Path(tmp))


if __name__ == "__main__":
    unittest.main()
