from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SECURITY_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SECURITY_ROOT.parents[1]
if str(SECURITY_ROOT) not in sys.path:
    sys.path.insert(0, str(SECURITY_ROOT))

import scanner  # noqa: E402

MODULE_PATH = SECURITY_ROOT / "scan_git_history.py"
SPEC = importlib.util.spec_from_file_location("scan_git_history_hardening", MODULE_PATH)
assert SPEC and SPEC.loader
history = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = history
SPEC.loader.exec_module(history)


def _github_token() -> str:
    return bytes((103, 104, 112, 95)).decode("ascii") + ("A" * 36)


class GitHistoryFinalHardeningTests(unittest.TestCase):
    def test_shared_allowlist_parser_preserves_exact_whitespace_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            allowlist = Path(directory) / "allowlist.json"
            allowlist.write_text(
                json.dumps(
                    {
                        "schema_version": "nexus_secret_scan_allowlist_v1",
                        "entries": [
                            {
                                "path": " runtime.py",
                                "rule": "github_token",
                                "fingerprint": "0123456789abcdef",
                                "reason": "Synthetic whitespace path fixture.",
                                "expires_on": "2099-12-31",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            entries = scanner.load_allowlist(allowlist)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].path, " runtime.py")
        self.assertEqual(entries[0].key[0], " runtime.py")

    def test_duplicate_identical_same_line_occurrences_receive_distinct_fingerprints(self) -> None:
        token = _github_token()
        line = f'FIRST = "{token}"; SECOND = "{token}"'

        findings = list(history._iter_secret_findings("runtime.py", line))

        self.assertEqual(len(findings), 2)
        self.assertEqual([finding.rule for finding in findings], ["github_token", "github_token"])
        self.assertEqual(len({finding.fingerprint for finding in findings}), 2)

    def test_credential_shaped_suffix_is_not_emitted(self) -> None:
        suffix_secret = "sk-" + ("A" * 20)
        evidence = history._path_evidence(f"runtime.{suffix_secret}")
        encoded = json.dumps(evidence, sort_keys=True)

        self.assertEqual(len(evidence["path_sha256"]), 64)
        self.assertEqual(evidence["path_suffix"], "")
        self.assertNotIn(suffix_secret, encoded)

    def test_workflow_never_uploads_tainted_history_report(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "git-history-secret-assurance.yml").read_text(
            encoding="utf-8"
        )

        clean_step = workflow.index("- name: Upload clean bounded history evidence")
        failure_step = workflow.index("- name: Upload sanitized history failure status")
        enforce_step = workflow.index("- name: Enforce complete clean history assurance")
        self.assertLess(clean_step, failure_step)
        self.assertLess(failure_step, enforce_step)
        self.assertIn("steps.artifact_scan.outputs.exit_code == '0'", workflow[clean_step:failure_step])
        self.assertIn("security-git-history-scan.json", workflow[clean_step:failure_step])
        self.assertIn("steps.artifact_scan.outputs.exit_code != '0'", workflow[failure_step:enforce_step])
        self.assertIn("security-git-history-exit-status.json", workflow[failure_step:enforce_step])
        self.assertNotIn("security-git-history-scan.json", workflow[failure_step:enforce_step])


if __name__ == "__main__":
    unittest.main()
