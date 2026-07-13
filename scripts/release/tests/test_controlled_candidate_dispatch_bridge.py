from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BRIDGE = (ROOT / ".github/workflows/controlled-candidate-dispatch-bridge.yml").read_text(encoding="utf-8")
REQUEST = json.loads((ROOT / ".github/controlled-candidate-request.json").read_text(encoding="utf-8"))


class ControlledCandidateDispatchBridgeTests(unittest.TestCase):
    def test_bridge_is_exact_main_path_bounded_and_least_privilege(self) -> None:
        self.assertIn("push:", BRIDGE)
        self.assertIn("branches:\n      - main", BRIDGE)
        self.assertIn("paths:\n      - .github/controlled-candidate-request.json", BRIDGE)
        self.assertNotIn("workflow_dispatch:", BRIDGE)
        self.assertNotIn("pull_request:", BRIDGE)
        self.assertNotIn("issue_comment:", BRIDGE)
        self.assertIn("permissions: {}", BRIDGE)
        self.assertIn("actions: write", BRIDGE)
        self.assertIn("contents: read", BRIDGE)
        self.assertIn("issues: write", BRIDGE)
        self.assertNotIn("packages: write", BRIDGE)
        self.assertNotIn("attestations: write", BRIDGE)
        self.assertNotIn("id-token: write", BRIDGE)

    def test_bridge_is_pinned_and_rejects_stale_or_unbounded_requests(self) -> None:
        self.assertIn(
            "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5",
            BRIDGE,
        )
        for mutable in ("@main", "@master", "@v1", "@v2", "@v3", "@v4"):
            self.assertNotIn(mutable, BRIDGE)
        for marker in (
            'test "$GITHUB_EVENT_NAME" = "push"',
            'test "$GITHUB_REF" = "refs/heads/main"',
            'test "$(git rev-parse origin/main)" = "$SOURCE_SHA"',
            'parent_sha="$(git rev-parse "${SOURCE_SHA}^1")"',
            'git diff --name-only "$parent_sha" "$SOURCE_SHA" -- "$REQUEST_PATH"',
            'test "$(jq -er \'.base_sha\' "$REQUEST_PATH")" = "$parent_sha"',
            'test "$(jq -er \'.issue_533\' "$REQUEST_PATH")" = "NO_GO"',
            "(.deployment_authorized == false)",
            "(.production_authority == false)",
            "(.external_actions_authorized == false)",
        ):
            self.assertIn(marker, BRIDGE)

    def test_bridge_dispatches_only_the_existing_manual_candidate_workflow(self) -> None:
        self.assertIn(
            '"repos/${GH_REPO}/actions/workflows/controlled-candidate-convergence.yml/dispatches"',
            BRIDGE,
        )
        self.assertIn("--method POST", BRIDGE)
        self.assertIn("--raw-field ref=main", BRIDGE)
        self.assertNotIn("docker build", BRIDGE)
        self.assertNotIn("docker push", BRIDGE)
        self.assertNotIn("gh release", BRIDGE)

    def test_bridge_reports_only_the_exact_dispatched_source_run(self) -> None:
        for marker in (
            "controlled-candidate-convergence.yml/runs?branch=main&event=workflow_dispatch&per_page=20",
            'select(.head_sha == $sha and .event == "workflow_dispatch" and .head_branch == "main")',
            '[[ "$run_id" =~ ^[0-9]+$ ]]',
            '"repos/${GH_REPO}/issues/724/comments"',
            "## CONTROLLED_CANDIDATE_RUN",
            "- Deployment performed: `false`",
            "- External actions authorized: `false`",
            "- #533 GO: `false`",
        ):
            self.assertIn(marker, BRIDGE)
        self.assertNotIn('"repos/${GH_REPO}/issues/714/comments"', BRIDGE)
        self.assertIn("for _attempt in $(seq 1 45)", BRIDGE)
        self.assertIn("sleep 2", BRIDGE)

    def test_request_is_bounded_and_preserves_no_go(self) -> None:
        self.assertEqual(REQUEST["schema"], "nexus.osr.controlled-candidate-request.v1")
        self.assertEqual(REQUEST["intent"], "dispatch-controlled-candidate")
        self.assertEqual(REQUEST["candidate_workflow"], "controlled-candidate-convergence.yml")
        self.assertEqual(REQUEST["requested_ref"], "main")
        self.assertRegex(REQUEST["request_id"], r"^[a-z0-9][a-z0-9._-]{7,63}$")
        self.assertRegex(REQUEST["base_sha"], r"^[0-9a-f]{40}$")
        self.assertIs(REQUEST["deployment_authorized"], False)
        self.assertIs(REQUEST["production_authority"], False)
        self.assertIs(REQUEST["external_actions_authorized"], False)
        self.assertEqual(REQUEST["issue_533"], "NO_GO")
        self.assertEqual(set(REQUEST), {
            "schema",
            "request_id",
            "intent",
            "candidate_workflow",
            "requested_ref",
            "base_sha",
            "deployment_authorized",
            "production_authority",
            "external_actions_authorized",
            "issue_533",
        })


if __name__ == "__main__":
    unittest.main()
