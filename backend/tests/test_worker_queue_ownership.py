from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKGROUND_BOUNDARY = (
    ROOT / "backend/app/services/background_job_transaction_boundary.py"
)
WORKER_RUNNER = ROOT / "backend/scripts/run_worker.py"
CONTROLLED_COMPOSE = ROOT / "deploy/docker-compose.controlled.yml"


def _function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"missing function: {name}")


def test_background_worker_claims_only_background_owned_job_types():
    function = _function_source(
        BACKGROUND_BOUNDARY,
        "dispatch_pending_background_jobs",
    )
    for required in (
        "AUTO_REPLY_JOB",
        "ATTACHMENT_PERSIST_JOB",
        "SPEEDAF_WORK_ORDER_CREATE_JOB",
        "SPEEDAF_ADDRESS_UPDATE_JOB",
        "SPEEDAF_VOICE_CALLBACK_JOB",
        "EMAIL_MAILBOX_SYNC_JOB",
    ):
        assert required in function
    assert "WEBCHAT_AI_REPLY_JOB" not in function
    assert "WEBCHAT_HANDOFF_SNAPSHOT_JOB" not in function
    assert "EXTERNAL_CHANNEL_SYNC_JOB" not in function


def test_dedicated_dispatchers_own_webchat_ai_and_handoff_snapshot():
    runner = WORKER_RUNNER.read_text(encoding="utf-8")
    assert "dispatch_pending_webchat_ai_reply_jobs" in runner
    assert "dispatch_pending_webchat_handoff_snapshot_jobs" in runner
    assert 'if queue in {"all", "webchat-ai"}' in runner
    assert 'if queue in {"all", "handoff-snapshot"}' in runner


def test_controlled_services_use_one_queue_per_worker():
    compose = CONTROLLED_COMPOSE.read_text(encoding="utf-8")
    assert "--queue outbound" in compose
    assert "--queue background" in compose
    assert "--queue webchat-ai" in compose
    assert "--queue handoff-snapshot" in compose
    controlled_services = compose.split("services:", 1)[1]
    assert "--queue all" not in controlled_services
