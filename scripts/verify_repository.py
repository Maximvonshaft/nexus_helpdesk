#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "scripts" / "repository_verification_core.py"
SPEC = importlib.util.spec_from_file_location("nexus_repository_verification_core", CORE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("repository_verification_core_unavailable")
CORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CORE)

CANONICAL_WORKFLOW = ".github/workflows/canonical-acceptance.yml"
CONTROLLED_CANDIDATE_WORKFLOW = (
    ".github/workflows/controlled-candidate-convergence.yml"
)
APPROVED_WORKFLOWS = sorted(
    [CANONICAL_WORKFLOW, CONTROLLED_CANDIDATE_WORKFLOW]
)


def _job_has_timeout(content: str, job_name: str) -> bool:
    marker = f"  {job_name}:"
    start = content.find(marker)
    if start < 0:
        return False
    remainder = content[start + len(marker) :]
    next_job = re.search(r"(?m)^  [A-Za-z0-9_-]+:\s*$", remainder)
    block = remainder[: next_job.start()] if next_job else remainder
    return "timeout-minutes:" in block


def _workflow_failures() -> list[str]:
    workflow_dir = ROOT / ".github" / "workflows"
    files = (
        sorted(
            path.relative_to(ROOT).as_posix()
            for path in workflow_dir.rglob("*")
            if path.is_file()
        )
        if workflow_dir.is_dir()
        else []
    )
    failures: list[str] = []
    if files != APPROVED_WORKFLOWS:
        failures.append(
            "GitHub Actions authority set is invalid: "
            f"expected={APPROVED_WORKFLOWS} actual={files}"
        )
        return failures

    canonical = (ROOT / CANONICAL_WORKFLOW).read_text(encoding="utf-8")
    for marker in (
        "name: Canonical Acceptance",
        "pull_request:",
        "push:",
        "workflow_dispatch:",
        "contents: read",
        "required-gate:",
        "python scripts/verify_repository.py --static-only",
        "python scripts/qualification/postgres_acceptance.py",
        "npm run verify",
        "npm run e2e",
        "docker build",
    ):
        if marker not in canonical:
            failures.append(f"canonical workflow marker missing: {marker}")
    for forbidden in (
        "pull_request_target:",
        "paths-ignore:",
        "secrets.",
        "continue-on-error: true",
    ):
        if forbidden in canonical:
            failures.append(
                f"canonical workflow contains forbidden marker: {forbidden}"
            )

    candidate = (ROOT / CONTROLLED_CANDIDATE_WORKFLOW).read_text(
        encoding="utf-8"
    )
    for marker in (
        "name: controlled-candidate-convergence",
        "workflow_run:",
        "- Canonical Acceptance",
        "github.event.workflow_run.conclusion == 'success'",
        "github.event.workflow_run.event == 'push'",
        "github.event.workflow_run.head_branch == 'main'",
        "test \"$(git rev-parse origin/main)\" = \"$SOURCE_SHA\"",
        "scripts/release/run_controlled_rc_gate.sh",
        "scripts/release/run_controlled_recovery_gate.sh",
        "actions/attest-build-provenance@",
        "controlled-candidate.env",
        "nexus.canonical-acceptance-receipt.v1",
    ):
        if marker not in candidate:
            failures.append(
                f"controlled candidate workflow marker missing: {marker}"
            )
    for forbidden in (
        "pull_request:",
        "pull_request_target:",
        "workflow_dispatch:",
        "issue_comment:",
        "repository_dispatch:",
        "continue-on-error: true",
    ):
        if forbidden in candidate:
            failures.append(
                "controlled candidate workflow contains forbidden marker: "
                f"{forbidden}"
            )

    for label, content in (
        ("canonical", canonical),
        ("controlled_candidate", candidate),
    ):
        for line in (
            line
            for line in content.splitlines()
            if re.match(r"^\s*-?\s*uses:", line)
        ):
            if not CORE.PINNED_ACTION.fullmatch(line):
                failures.append(
                    f"{label} workflow action is not pinned to a full SHA: "
                    f"{line.strip()}"
                )

    canonical_job_count = len(
        re.findall(r"(?m)^  [A-Za-z0-9_-]+:\s*$", canonical.split("jobs:", 1)[-1])
    )
    if canonical_job_count == 0 or canonical.count("timeout-minutes:") < canonical_job_count:
        failures.append("every canonical acceptance job must set timeout-minutes")

    for job_name in (
        "guard-main",
        "build-assure-publish",
        "recovery",
        "bind-and-attest",
    ):
        if not _job_has_timeout(candidate, job_name):
            failures.append(
                f"controlled candidate workflow job timeout missing: {job_name}"
            )
    return failures


CORE._workflow_failures = _workflow_failures
main = CORE.main


if __name__ == "__main__":
    raise SystemExit(main())
