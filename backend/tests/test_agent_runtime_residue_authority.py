from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_only_one_specialist_execution_authority_exists() -> None:
    runtime_dir = ROOT / "backend/app/services/agent_runtime"
    assert (runtime_dir / "specialist_runtime.py").exists()
    assert not (runtime_dir / "specialist_service.py").exists()

    tool_handlers = _source("backend/app/services/agent_tool_handlers.py")
    tool_contracts = _source("backend/app/services/agent_tool_contracts.py")
    specialist_runtime = _source(
        "backend/app/services/agent_runtime/specialist_runtime.py"
    )
    assert tool_handlers.count('"specialist.delegate"') == 2
    assert tool_contracts.count('"specialist.delegate"') == 2
    assert "ProviderRuntimeRouter(db).route(request)" in specialist_runtime
    assert "threading" not in specialist_runtime
    assert "Thread(" not in specialist_runtime


def test_specialist_lifecycle_reuses_tool_and_provider_evidence() -> None:
    run_events = _source("backend/app/services/agent_runtime/run_events.py")
    for duplicate_event in (
        "specialist_started",
        "specialist_completed",
        "specialist_failed",
    ):
        assert duplicate_event not in run_events
    assert '"tool_started"' in run_events
    assert '"tool_completed"' in run_events
    assert '"tool_failed"' in run_events


def test_fork_tools_are_intersected_with_exact_release_authority() -> None:
    api = _source("backend/app/api/agent_runtime_operations.py")
    assert 'read_tools = _read_only_tools(context.get("agent_release_snapshot"))' in api
    assert 'resolved.get("allowed_tools")' in api
    assert 'detail="agent_fork_exact_release_not_resolved"' in api
    assert '"integration.write"' not in api


def test_context_compiler_has_no_serialized_tail_slice() -> None:
    compiler = _source("backend/app/services/agent_runtime/context_compiler.py")
    adapter = _source(
        "backend/app/services/provider_runtime/adapters/private_ai_runtime.py"
    )
    assert "prompt[:" not in compiler
    assert ")[: profile.max_prompt_chars]" not in adapter
    assert "compile_agent_context(" in adapter


def test_session_checkpoint_models_are_in_the_single_registry() -> None:
    registry = _source("backend/app/model_registry.py")
    assert '"app.models_agent_runtime"' in registry
    assert '"agent_session_checkpoints"' in registry
