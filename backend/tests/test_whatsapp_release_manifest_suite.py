from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SUITE = (
    ROOT
    / "scripts"
    / "release"
    / "tests"
    / "test_build_controlled_candidate_manifest.py"
)


def test_whatsapp_sidecar_is_bound_by_controlled_candidate_manifest() -> None:
    completed = subprocess.run(
        [sys.executable, str(SUITE)],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout[-8000:]
