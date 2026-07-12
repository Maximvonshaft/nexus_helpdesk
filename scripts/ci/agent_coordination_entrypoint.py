#!/usr/bin/env python3
"""Trusted CLI entrypoint for the final Nexus OSR coordination policy."""
from __future__ import annotations

from typing import Sequence

import agent_coordination_policy_gate as final_policy
import agent_coordination_path_policy as path_policy
import agent_coordination_open_pr_policy as open_pr_policy


def main(argv: Sequence[str] | None = None) -> int:
    """Run the base gate without allowing lower adapters to replace policy."""

    open_pr_policy.install_open_pr_policy()
    final_policy.policy.gate.load_snapshot = final_policy.policy.load_snapshot_with_reclaim
    final_policy.policy.gate.evaluate_snapshot = open_pr_policy._evaluate_snapshot_policy
    return final_policy.policy.gate.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
