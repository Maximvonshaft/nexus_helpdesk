from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _run_contract_suite(directory: str) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            directory,
            "-p",
            "test_*.py",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout


def test_release_and_deployment_contract_suites_are_part_of_backend_acceptance() -> None:
    _run_contract_suite("scripts/release/tests")
    _run_contract_suite("scripts/deploy/tests")
