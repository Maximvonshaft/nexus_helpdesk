from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_agent_runtime_has_no_retired_business_gate_residue():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(root / "scripts/ci/check_agent_runtime_residue.py")],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
