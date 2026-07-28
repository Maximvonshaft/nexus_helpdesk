from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_release_deployment_and_remediation_contract_suites_are_canonical_backend_gates() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "backend/tests/test_whatsapp_embedded_signup_retryability.py",
            "backend/tests/test_ticketless_whatsapp_delivery.py",
            "scripts/release/tests",
            "scripts/deploy/tests",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stdout
