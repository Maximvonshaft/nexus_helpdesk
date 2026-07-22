from __future__ import annotations

import ast
from pathlib import Path

from app.services.background_job_transaction_boundary import (
    commit_webchat_agent_provider_boundary,
)

ROOT = Path(__file__).resolve().parents[2]


class _FakeSession:
    bind = object()

    def __init__(self) -> None:
        self.commits = 0

    def execute(self, *_args, **_kwargs):
        return None

    def commit(self) -> None:
        self.commits += 1


def test_provider_boundary_commits_inflight_state_once() -> None:
    db = _FakeSession()
    commit_webchat_agent_provider_boundary(db)
    assert db.commits == 1


def test_orchestration_delegates_provider_commit_to_attempt_boundary() -> None:
    relative = "backend/app/services/webchat_ai_orchestration_service.py"
    source = (ROOT / relative).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=relative)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "process_webchat_ai_reply_job"
    )
    segment = ast.get_source_segment(source, function) or ""
    boundary = "commit_webchat_agent_provider_boundary(db)"
    provider_call = "result = _run_agent_reply("
    assert boundary in segment
    assert provider_call in segment
    assert segment.index(boundary) < segment.index(provider_call)
    assert "db.commit()" not in segment
