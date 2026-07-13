from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SECURITY_ROOT = Path(__file__).resolve().parents[1]
if str(SECURITY_ROOT) not in sys.path:
    sys.path.insert(0, str(SECURITY_ROOT))

import scanner  # noqa: E402

MODULE_PATH = SECURITY_ROOT / "scan_git_history.py"


def _load_history_module():
    if not MODULE_PATH.is_file():
        raise ImportError(f"history scanner module is missing: {MODULE_PATH}")
    spec = importlib.util.spec_from_file_location("scan_git_history", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError("history scanner module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


history = _load_history_module()


def _github_token(fill: str = "A") -> str:
    return bytes((103, 104, 112, 95)).decode("ascii") + (fill * 36)


def _run(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _run_with_input(root: Path, args: list[str], data: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        input=data,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _init_repo(root: Path) -> None:
    _run(root, "init", "-b", "main")
    _run(root, "config", "user.name", "Nexus Test")
    _run(root, "config", "user.email", "nexus-test@example.invalid")


def _commit_all(root: Path, message: str) -> None:
    _run(root, "add", "-A")
    _run(root, "commit", "-m", message)


def _scan(root: Path, *, max_blob_bytes: int = scanner.MAX_FILE_BYTES):
    return history.scan_repository_history(
        root,
        allowlist_path=root / "missing-allowlist.json",
        max_blob_bytes=max_blob_bytes,
    )


def _write_allowlist(root: Path, entries: list[dict[str, str]]) -> Path:
    path = root / "allowlist.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "nexus_secret_scan_allowlist_v1",
                "entries": entries,
            }
        ),
        encoding="utf-8",
    )
    return path


def _fingerprint_for(rule: str, path: str, line_no: int, line: str) -> str:
    pattern = dict(scanner._PATTERNS)[rule]
    match = pattern.search(line)
    if match is None:
        raise AssertionError(f"fixture did not match {rule}")
    return scanner._fingerprint(rule, path, line_no, match.group(0))


class GitHistorySecretAssuranceTests(unittest.TestCase):
    def test_secret_removed_from_head_is_still_detected_without_raw_value_or_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            token = _github_token()
            secret_path = root / "runtime.py"
            secret_path.write_text("TOKEN = " + json.dumps(token) + "\n", encoding="utf-8")
            _commit_all(root, "add historical token")
            secret_path.unlink()
            (root / "README.md").write_text("clean head\n", encoding="utf-8")
            _commit_all(root, "remove historical token")

            report = _scan(root)
            encoded = json.dumps(report, sort_keys=True)

        finding = report["findings"][0]
        self.assertEqual(report["status"], "fail")
        self.assertTrue(report["complete"])
        self.assertEqual(report["finding_count"], 1)
        self.assertEqual(finding["rule"], "github_token")
        self.assertEqual(len(finding["path_sha256"]), 64)
        self.assertEqual(finding["path_suffix"], ".py")
        self.assertIn(len(finding["blob_sha"]), {40, 64})
        self.assertNotIn("path", finding)
        self.assertNotIn(token, encoded)
        self.assertNotIn("runtime.py", encoded)
        self.assertNotIn("add historical token", encoded)
        self.assertNotIn("refs/heads", encoded)

    def test_finding_report_passes_generic_artifact_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            (root / "runtime.py").write_text(
                "TOKEN = " + json.dumps(_github_token()) + "\n",
                encoding="utf-8",
            )
            _commit_all(root, "add token")
            report = _scan(root)
            report_path = root / "history-report.json"
            report_path.write_text(
                json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            artifact_findings = scanner.scan_artifact_files(root, ["history-report.json"])

        self.assertEqual(artifact_findings, [])
        self.assertIsInstance(report["by_rule"], list)
        self.assertEqual(report["by_rule"], [{"rule": "github_token", "count": 1}])

    def test_placeholder_history_is_not_a_finding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            (root / "example.md").write_text(
                "example token = " + _github_token() + "\n",
                encoding="utf-8",
            )
            _commit_all(root, "add example placeholder")
            report = _scan(root)

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["finding_count"], 0)

    def test_unchanged_blob_is_scanned_once_across_commits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            (root / "runtime.py").write_text(
                "TOKEN = " + json.dumps(_github_token()) + "\n",
                encoding="utf-8",
            )
            _commit_all(root, "add token")
            (root / "README.md").write_text("one\n", encoding="utf-8")
            _commit_all(root, "unrelated one")
            (root / "README.md").write_text("two\n", encoding="utf-8")
            _commit_all(root, "unrelated two")
            report = _scan(root)

        self.assertEqual(report["finding_count"], 1)
        self.assertEqual(report["findings_truncated"], False)
        self.assertLess(report["scanned_text_blob_count"], report["reachable_object_count"])

    def test_same_finding_in_changed_blob_is_logically_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            path = root / "runtime.py"
            token_line = "TOKEN = " + json.dumps(_github_token()) + "\n"
            path.write_text(token_line, encoding="utf-8")
            _commit_all(root, "add token")
            path.write_text(token_line + "# unrelated\n", encoding="utf-8")
            _commit_all(root, "unrelated content")
            report = _scan(root)

        self.assertEqual(report["finding_count"], 1)
        self.assertEqual(len(report["findings"]), 1)

    def test_allowlisting_one_path_does_not_suppress_a_same_blob_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            line = "TOKEN = " + json.dumps(_github_token())
            (root / "fixture.py").write_text(line + "\n", encoding="utf-8")
            (root / "runtime.py").write_text(line + "\n", encoding="utf-8")
            _commit_all(root, "add identical fixture and runtime blobs")
            allowlist_path = _write_allowlist(
                root,
                [
                    {
                        "path": "fixture.py",
                        "rule": "github_token",
                        "fingerprint": _fingerprint_for("github_token", "fixture.py", 1, line),
                        "reason": "Synthetic fixture path only.",
                        "expires_on": "2099-12-31",
                    }
                ],
            )
            report = history.scan_repository_history(root, allowlist_path=allowlist_path)

        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["suppressed_count"], 1)
        self.assertEqual(report["finding_count"], 1)
        self.assertEqual(
            report["findings"][0]["path_sha256"],
            hashlib.sha256(b"runtime.py").hexdigest(),
        )

    def test_direct_tree_tag_paths_receive_independent_allowlist_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            line = "TOKEN = " + json.dumps(_github_token())
            fixture = root / "fixture.py"
            fixture.write_text(line + "\n", encoding="utf-8")
            _commit_all(root, "add fixture blob")
            blob_sha = _run(root, "hash-object", "fixture.py")
            tree_sha = _run_with_input(
                root,
                ["mktree"],
                f"100644 blob {blob_sha}\truntime.py\n",
            )
            _run(root, "update-ref", "refs/tags/tree-snapshot", tree_sha)
            allowlist_path = _write_allowlist(
                root,
                [
                    {
                        "path": "fixture.py",
                        "rule": "github_token",
                        "fingerprint": _fingerprint_for("github_token", "fixture.py", 1, line),
                        "reason": "Synthetic fixture path only.",
                        "expires_on": "2099-12-31",
                    }
                ],
            )
            report = history.scan_repository_history(root, allowlist_path=allowlist_path)

        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["suppressed_count"], 1)
        self.assertEqual(report["finding_count"], 1)
        self.assertEqual(
            report["findings"][0]["path_sha256"],
            hashlib.sha256(b"runtime.py").hexdigest(),
        )

    def test_leading_space_path_is_not_collapsed_into_allowlisted_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            line = "TOKEN = " + json.dumps(_github_token())
            (root / "runtime.py").write_text(line + "\n", encoding="utf-8")
            (root / " runtime.py").write_text(line + "\n", encoding="utf-8")
            _commit_all(root, "add whitespace alias")
            allowlist_path = _write_allowlist(
                root,
                [
                    {
                        "path": "runtime.py",
                        "rule": "github_token",
                        "fingerprint": _fingerprint_for("github_token", "runtime.py", 1, line),
                        "reason": "Synthetic fixture path only.",
                        "expires_on": "2099-12-31",
                    }
                ],
            )
            report = history.scan_repository_history(root, allowlist_path=allowlist_path)

        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["suppressed_count"], 1)
        self.assertEqual(report["finding_count"], 1)
        self.assertEqual(
            report["findings"][0]["path_sha256"],
            hashlib.sha256(b" runtime.py").hexdigest(),
        )

    def test_all_same_rule_matches_on_one_line_are_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            line = f'FIRST = "{_github_token("A")}"; SECOND = "{_github_token("B")}"'
            (root / "runtime.py").write_text(line + "\n", encoding="utf-8")
            _commit_all(root, "add two same-rule values")
            report = _scan(root)

        self.assertEqual(report["finding_count"], 2)
        self.assertEqual(report["by_rule"], [{"rule": "github_token", "count": 2}])
        self.assertEqual(len({finding["fingerprint"] for finding in report["findings"]}), 2)

    def test_history_counts_all_findings_while_tree_scan_keeps_existing_cap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            token = json.dumps(_github_token())
            (root / "many.py").write_text(
                "".join(f"TOKEN_{index} = {token}\n" for index in range(205)),
                encoding="utf-8",
            )
            _commit_all(root, "add many findings")
            report = _scan(root)
            tree_findings = scanner.scan_secret_files(root, ["many.py"])

        self.assertEqual(report["finding_count"], 205)
        self.assertEqual(len(report["findings"]), history.MAX_STORED_FINDINGS)
        self.assertTrue(report["findings_truncated"])
        self.assertEqual(len(tree_findings), scanner.MAX_FINDINGS)

    def test_unknown_oversized_blob_makes_scan_incomplete_with_safe_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            (root / "large.txt").write_text("A" * 128, encoding="utf-8")
            _commit_all(root, "add oversized text")
            report = _scan(root, max_blob_bytes=32)

        self.assertEqual(report["status"], "fail")
        self.assertFalse(report["complete"])
        self.assertEqual(report["unscanned_oversized_blob_count"], 1)
        self.assertEqual(report["accounted_blob_count"], report["reachable_blob_count"])
        self.assertEqual(len(report["unscanned_oversized"]), 1)
        item = report["unscanned_oversized"][0]
        self.assertEqual(len(item["path_sha256"]), 64)
        self.assertEqual(item["path_suffix"], ".txt")
        self.assertEqual(item["size_bytes"], 128)
        self.assertNotIn("path", item)

    def test_oversized_binary_suffix_is_not_treated_as_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            payload = ("TOKEN = " + json.dumps(_github_token()) + "\n") * 4
            (root / "image.png").write_text(payload, encoding="utf-8")
            _commit_all(root, "add oversized suffix fixture")
            report = _scan(root, max_blob_bytes=32)

        self.assertEqual(report["status"], "fail")
        self.assertFalse(report["complete"])
        self.assertEqual(report["oversized_binary_blob_count"], 0)
        self.assertEqual(report["unscanned_oversized_blob_count"], 1)
        self.assertEqual(report["accounted_blob_count"], report["reachable_blob_count"])

    def test_exact_allowlist_suppresses_history_finding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            line = "TOKEN = " + json.dumps(_github_token())
            (root / "runtime.py").write_text(line + "\n", encoding="utf-8")
            _commit_all(root, "add token")
            allowlist_path = _write_allowlist(
                root,
                [
                    {
                        "path": "runtime.py",
                        "rule": "github_token",
                        "fingerprint": _fingerprint_for("github_token", "runtime.py", 1, line),
                        "reason": "Synthetic history scanner fixture only.",
                        "expires_on": "2099-12-31",
                    }
                ],
            )
            report = history.scan_repository_history(root, allowlist_path=allowlist_path)

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["finding_count"], 0)
        self.assertEqual(report["suppressed_count"], 1)

    def test_reference_digest_and_report_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            (root / "README.md").write_text("clean\n", encoding="utf-8")
            _commit_all(root, "initial")
            _run(root, "tag", "v1")
            first = _scan(root)
            second = _scan(root)

        self.assertEqual(first, second)
        self.assertEqual(len(first["refs_sha256"]), 64)
        self.assertIn(len(first["source_sha"]), {40, 64})

    def test_shallow_repository_is_rejected_before_scanning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            source = parent / "source"
            source.mkdir()
            _init_repo(source)
            (source / "README.md").write_text("one\n", encoding="utf-8")
            _commit_all(source, "one")
            (source / "README.md").write_text("two\n", encoding="utf-8")
            _commit_all(source, "two")
            shallow = parent / "shallow"
            subprocess.run(
                ["git", "clone", "--depth", "1", source.as_uri(), str(shallow)],
                check=True,
                capture_output=True,
            )

            with self.assertRaisesRegex(history.HistoryScanError, "git_repository_shallow"):
                _scan(shallow)

    def test_malformed_object_listing_fails_closed(self) -> None:
        with self.assertRaisesRegex(history.HistoryScanError, "git_object_listing_invalid"):
            history.parse_object_listing(b"not-an-object-id path.txt\n", object_id_length=40)

    def test_failure_report_is_bounded_and_contains_no_raw_git_error(self) -> None:
        report = history.failure_report("git_command_failed", object_id_length=40)
        encoded = json.dumps(report, sort_keys=True)

        self.assertEqual(report["status"], "fail")
        self.assertFalse(report["complete"])
        self.assertEqual(report["failure_reason"], "git_command_failed")
        self.assertLess(len(encoded.encode("utf-8")), 4096)
        self.assertNotIn("stderr", encoded.lower())


if __name__ == "__main__":
    unittest.main()
