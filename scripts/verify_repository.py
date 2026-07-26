#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "scripts" / "repository_verification_core.py"
SPEC = importlib.util.spec_from_file_location(
    "nexus_repository_verification_core",
    CORE_PATH,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("repository_verification_core_unavailable")
CORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CORE)
main = CORE.main


if __name__ == "__main__":
    raise SystemExit(main())
