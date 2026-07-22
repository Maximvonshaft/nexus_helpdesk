from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def test_canonical_telephony_residue_gate_is_clean():
    completed = subprocess.run(
        [sys.executable, "scripts/ci/check_telephony_authority_residue.py"],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
