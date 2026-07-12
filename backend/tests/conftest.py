from __future__ import annotations

from datetime import datetime, timezone

import pytest


@pytest.fixture(autouse=True)
def freeze_escalation_hook_human_hours_clock(request, monkeypatch):
    """Remove a minute-boundary wall-clock dependency from one test module.

    Production Human Hours behavior is unchanged. The patch applies only to the
    WebChat escalation-hook test module and only when callers omit an explicit
    ``now`` value. Explicit boundary tests still exercise the real timestamp.
    """

    node_path = getattr(request.node, "path", None)
    if node_path is None or node_path.name != "test_nexus_osr_webchat_escalation_hook.py":
        return

    from app.services.nexus_osr.policies import HumanHoursPolicy

    original_evaluate = HumanHoursPolicy.evaluate
    fixed_now = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)

    def evaluate_at_fixed_time(self, now=None):
        return original_evaluate(self, fixed_now if now is None else now)

    monkeypatch.setattr(HumanHoursPolicy, "evaluate", evaluate_at_fixed_time)
