from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

# Install the same final policies used by GitHub Actions before focused modules
# import the lower-level parser/evaluator or reclaim adapter.
import agent_coordination_policy_gate  # noqa: E402,F401
import agent_coordination_path_policy  # noqa: E402,F401
