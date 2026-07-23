from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _run_contract_suite(directory: str) -> None:
    result = subprocess.run(
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
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, (
        f"contract suite failed: {directory}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def test_release_and_deployment_contract_suites_are_part_of_backend_full() -> None:
    assert (ROOT / ".github/workflows/controlled-candidate-convergence.yml").is_file()
    assert (ROOT / ".github/workflows/controlled-candidate-dispatch-bridge.yml").is_file()
    _run_contract_suite("scripts/release/tests")
    _run_contract_suite("scripts/deploy/tests")
