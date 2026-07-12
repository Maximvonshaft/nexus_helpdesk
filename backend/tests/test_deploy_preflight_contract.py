from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = REPO_ROOT / "scripts" / "deploy" / "preflight.sh"


def _run_preflight(
    tmp_path: Path,
    *,
    readiness_exit_code: int,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    trace_file = tmp_path / "python-calls.log"
    fake_python = bin_dir / "python"
    fake_python.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$TRACE_FILE"
if [[ "${1:-}" == "scripts/validate_production_readiness.py" ]]; then
    exit "$READINESS_EXIT_CODE"
fi
cat >/dev/null
printf 'Preflight OK\\n'
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["TRACE_FILE"] = str(trace_file)
    env["READINESS_EXIT_CODE"] = str(readiness_exit_code)

    completed = subprocess.run(
        ["bash", str(PREFLIGHT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = (
        trace_file.read_text(encoding="utf-8").splitlines()
        if trace_file.exists()
        else []
    )
    return completed, calls


def test_preflight_propagates_readiness_failure_and_stops(tmp_path: Path) -> None:
    completed, calls = _run_preflight(tmp_path, readiness_exit_code=23)

    assert completed.returncode == 23
    assert calls == ["scripts/validate_production_readiness.py"]
    assert "Preflight OK" not in completed.stdout


def test_preflight_success_runs_both_validation_phases(tmp_path: Path) -> None:
    completed, calls = _run_preflight(tmp_path, readiness_exit_code=0)

    assert completed.returncode == 0
    assert calls == ["scripts/validate_production_readiness.py", "-"]
    assert completed.stdout.endswith("Preflight OK\n")
