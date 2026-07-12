from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = ROOT / "scripts" / "ci"
sys.path.insert(0, str(SCRIPT_DIR))

import agent_coordination_entrypoint as entrypoint  # noqa: E402
import agent_coordination_path_policy as path_policy  # noqa: E402
import agent_coordination_policy_gate as final_policy  # noqa: E402

FIXTURE = Path(__file__).with_name("fixtures") / "agent_coordination_snapshot.json"


def test_entrypoint_installs_final_evaluator_before_base_cli(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_gate_main(argv):
        observed["argv"] = argv
        observed["evaluate"] = final_policy.policy.gate.evaluate_snapshot
        observed["load"] = final_policy.policy.gate.load_snapshot
        observed["path_matches"] = final_policy.model._path_matches
        return 0

    monkeypatch.setattr(final_policy.policy.gate, "main", fake_gate_main)

    assert entrypoint.main(["--snapshot", str(FIXTURE)]) == 0
    assert observed["evaluate"] is final_policy._evaluate_snapshot_policy
    assert observed["load"] is final_policy.policy.load_snapshot_with_reclaim
    assert observed["path_matches"] is path_policy._path_matches


def test_entrypoint_fixture_produces_bounded_report(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    summary = tmp_path / "summary.md"

    result = entrypoint.main(
        [
            "--snapshot",
            str(FIXTURE),
            "--output",
            str(output),
            "--summary-path",
            str(summary),
        ]
    )

    assert result == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["state"] == "pass"
    assert report["bounded"] is True
    assert report["redacted"] is True
    assert "Agent Coordination Gate" in summary.read_text(encoding="utf-8")


def test_trusted_workflow_executes_entrypoint_from_base() -> None:
    trusted = (ROOT / ".github" / "workflows" / "agent-coordination-gate.yml").read_text(encoding="utf-8")
    self_test = (ROOT / ".github" / "workflows" / "agent-coordination-self-test.yml").read_text(encoding="utf-8")

    assert "pull_request_target" in trusted
    assert "ref: ${{ github.event.pull_request.base.sha }}" in trusted
    assert "python scripts/ci/agent_coordination_entrypoint.py" in trusted
    assert "Checkout proposed head" not in trusted

    assert "GITHUB_TOKEN" not in self_test
    assert "ref: ${{ github.event.pull_request.head.sha }}" in self_test
    assert "${{ github.event.pull_request.head.sha }}" in self_test
    assert "agent_coordination_entrypoint.py" in self_test
