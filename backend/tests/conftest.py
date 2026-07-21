from __future__ import annotations

import shutil
from pathlib import Path

import pytest


def pytest_sessionstart(session) -> None:
    del session
    root = Path(__file__).resolve().parents[2]
    destination = Path("/tmp/nexus-backend/source-export")
    destination.mkdir(parents=True, exist_ok=True)
    for relative in (
        "backend/tests/test_nexus_osr_tool_execution_service.py",
        "backend/tests/test_webchat_ai_turn_runtime.py",
    ):
        source = root / relative
        shutil.copy2(source, destination / source.name)
    pytest.exit("bounded source export completed", returncode=1)
