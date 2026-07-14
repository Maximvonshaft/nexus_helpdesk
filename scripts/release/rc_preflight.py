#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence

SCHEMA = "nexus.osr.rc-preflight.v1"
REGISTRY_PATH = Path("docs/ai/remote-skills-registry.yaml")
COMPILE_TARGETS = (
    "scripts/release/generate_rc_test_env.py",
    "scripts/release/seed_rc_test_data.py",
    "scripts/release/rc_test_http_smoke.py",
    "scripts/release/rc_test_side_effects.py",
    "scripts/release/build_rc_test_manifest.py",
    "scripts/release/validate_rc_test_manifest.py",
    "scripts/release/validate_rc_test_evidence.py",
    "scripts/release/rc_preflight.py",
)


def validate_registry_text(text: str) -> None:
    if not text.startswith("schema: nexus.osr.remote-skills-registry.v1\n"):
        raise ValueError("registry_schema_invalid")
    if "name: test_release_candidate_convergence" not in text:
        raise ValueError("registry_release_skill_missing")
    if "auto_upgrade: false" not in text:
        raise ValueError("registry_auto_upgrade_policy_missing")


def bounded_result(*, status: str, stage: str, exit_code: int, output: bytes = b"") -> dict[str, object]:
    if status not in {"pass", "fail"}:
        raise ValueError("status_invalid")
    return {
        "schema": SCHEMA,
        "status": status,
        "stage": stage,
        "exit_code": int(exit_code),
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "output_bytes": len(output),
    }


def _run(command: Sequence[str], *, timeout: int = 600) -> tuple[int, bytes]:
    completed = subprocess.run(
        list(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    output = completed.stdout or b""
    return int(completed.returncode), output[:1_000_000]


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def run_preflight(artifact_root: Path) -> int:
    failure_path = artifact_root / "failure-summary.json"
    result_path = artifact_root / "preflight-result.json"
    failure_path.unlink(missing_ok=True)

    try:
        validate_registry_text(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        payload = bounded_result(status="fail", stage="registry_contract", exit_code=1, output=str(exc).encode())
        _write(failure_path, payload)
        _write(result_path, payload)
        print("RC_PREFLIGHT_FAILED stage=registry_contract")
        return 1

    stages: tuple[tuple[str, Sequence[str]], ...] = (
        ("release_compile", (sys.executable, "-m", "py_compile", *COMPILE_TARGETS)),
        ("release_unit_tests", (sys.executable, "-m", "unittest", "discover", "-s", "scripts/release/tests")),
        ("release_shell_syntax", ("bash", "-n", "scripts/release/run_rc_test_candidate.sh")),
    )
    for stage, command in stages:
        try:
            exit_code, output = _run(command)
        except (OSError, subprocess.TimeoutExpired) as exc:
            output = str(exc).encode()
            exit_code = 124 if isinstance(exc, subprocess.TimeoutExpired) else 127
        if exit_code != 0:
            payload = bounded_result(status="fail", stage=stage, exit_code=exit_code, output=output)
            _write(failure_path, payload)
            _write(result_path, payload)
            print(f"RC_PREFLIGHT_FAILED stage={stage} exit_code={exit_code}")
            return exit_code or 1

    payload = bounded_result(status="pass", stage="complete", exit_code=0)
    _write(result_path, payload)
    print("RC_PREFLIGHT_PASSED")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run bounded, fail-closed RC preflight checks.")
    parser.add_argument("--artifact-root", type=Path, default=Path("artifacts/rc-test"))
    args = parser.parse_args(argv)
    return run_preflight(args.artifact_root)


if __name__ == "__main__":
    sys.exit(main())
