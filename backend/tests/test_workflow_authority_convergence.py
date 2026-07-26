from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE_PATH = ROOT / "scripts/repository_verification_core.py"
ENTRYPOINT_PATH = ROOT / "scripts/verify_repository.py"


def load_core():
    spec = importlib.util.spec_from_file_location("workflow_authority_core", CORE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_entrypoint_does_not_override_core_workflow_authority():
    source = ENTRYPOINT_PATH.read_text(encoding="utf-8")

    assert "CORE._workflow_failures" not in source
    assert "def _workflow_failures" not in source
    assert "main = CORE.main" in source


def test_core_is_the_only_workflow_authority_and_accepts_exact_two_stage_set():
    core = load_core()

    assert core.APPROVED_WORKFLOWS == sorted(
        [
            ".github/workflows/canonical-acceptance.yml",
            ".github/workflows/controlled-candidate-convergence.yml",
        ]
    )
    assert core._workflow_failures() == []
