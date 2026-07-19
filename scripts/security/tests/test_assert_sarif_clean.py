from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.security.assert_sarif_clean import SarifValidationError, _sarif_files, evaluate


class AssertSarifCleanTests(unittest.TestCase):
    def test_clean_sarif_passes_without_copying_source_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "python.sarif"
            path.write_text(json.dumps({"version": "2.1.0", "runs": [{"results": []}]}), encoding="utf-8")
            payload = evaluate([path])
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["result_count"], 0)
            self.assertFalse(payload["contains_source_snippets"])

    def test_any_codeql_result_fails_with_bounded_rule_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "javascript.sarif"
            path.write_text(
                json.dumps(
                    {
                        "version": "2.1.0",
                        "runs": [{"results": [{"ruleId": "js/sql-injection", "level": "error", "message": {"text": "secret source text"}}]}],
                    }
                ),
                encoding="utf-8",
            )
            payload = evaluate([path])
            self.assertEqual(payload["status"], "fail")
            self.assertEqual(payload["by_rule"], [{"rule_id": "js/sql-injection", "count": 1}])
            self.assertNotIn("secret source text", json.dumps(payload))

    def test_missing_sarif_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SarifValidationError):
                _sarif_files(Path(tmp))


if __name__ == "__main__":
    unittest.main()
