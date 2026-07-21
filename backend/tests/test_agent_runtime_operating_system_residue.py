from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
WEBAPP = REPO / "webapp"


def _tracked_text_files() -> list[Path]:
    roots = [
        BACKEND / "app",
        BACKEND / "alembic",
        WEBAPP / "src",
        REPO / "docs" / "architecture",
    ]
    output: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in {
                ".py",
                ".ts",
                ".tsx",
                ".json",
                ".md",
            }:
                output.append(path)
    return output


def _count(pattern: str, files: list[Path]) -> int:
    expression = re.compile(pattern)
    return sum(
        len(expression.findall(path.read_text(encoding="utf-8")))
        for path in files
    )


def test_one_runtime_event_and_checkpoint_authority() -> None:
    files = _tracked_text_files()
    assert _count(r"class\s+AgentRun\(Base\)", files) == 1
    assert _count(r"class\s+AgentRunEvent\(Base\)", files) == 1
    assert _count(r"class\s+AgentSessionCheckpoint\(Base\)", files) == 1
    assert _count(r"async\s+def\s+run_agent_with_db\s*\(", files) == 1
    assert _count(r"class\s+ControlledActionExecutor\s*:", files) == 1
    assert _count(r"export\s+function\s+AgentControlPage\s*\(", files) == 1


def test_retired_agent_paths_and_customer_memory_cannot_return() -> None:
    application = "\n".join(
        path.read_text(encoding="utf-8")
        for path in _tracked_text_files()
        if "test_agent_runtime_operating_system_residue.py" not in str(path)
    ).lower()
    for forbidden in (
        "agent_runtime.v1",
        "agent_runtime.v2",
        "customer_memory_service",
        "customermemoryfact",
        "agent_skills/skills.json",
        "agent_runtime/skill_registry.py",
        "static skill registry",
    ):
        assert forbidden not in application


def test_prompt_compiler_is_the_only_parent_and_specialist_budget_authority() -> None:
    adapter_path = (
        BACKEND
        / "app/services/provider_runtime/adapters/private_ai_runtime.py"
    )
    adapter_source = adapter_path.read_text(encoding="utf-8")
    tree = ast.parse(adapter_source)
    build_prompt = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_build_prompt"
    )
    body = ast.get_source_segment(adapter_source, build_prompt) or ""
    assert "compile_agent_context" in body
    assert "[: profile.max_prompt_chars]" not in body
    compiler = (
        BACKEND / "app/services/agent_runtime/context_compiler.py"
    ).read_text(encoding="utf-8")
    assert "def compile_agent_context(" in compiler
    assert "_compile_parent_agent_context" in compiler
    assert "_compile_specialist_context" in compiler
    assert "agent_context_mandatory_budget_exceeded" in compiler


def test_mcp_transport_has_one_lifecycle_and_no_runtime_discovery_authority() -> None:
    files = _tracked_text_files()
    direct_calls = []
    for path in files:
        source = path.read_text(encoding="utf-8")
        if 'method="tools/call"' in source or '"method": "tools/call"' in source:
            direct_calls.append(path.relative_to(REPO).as_posix())
    assert direct_calls == ["backend/app/services/agent_integration_service.py"]
    integration_source = (
        BACKEND / "app/services/agent_integration_service.py"
    ).read_text(encoding="utf-8")
    for required in (
        'method="initialize"',
        'method="notifications/initialized"',
        'method="tools/list"',
        'method="tools/call"',
        "schema_mismatches",
        "unmanaged_tools",
    ):
        assert required in integration_source
    assert "integration_operation_classification_mismatch" in integration_source


def test_specialist_is_one_read_only_tool_not_a_parallel_runtime() -> None:
    contracts = (
        BACKEND / "app/services/agent_tool_contracts.py"
    ).read_text(encoding="utf-8")
    block = contracts.split('"specialist.delegate":', 1)[1]
    assert 'classification="read"' in block
    assert "customer_visible_result=False" in block
    assert '"objective"' in block
    assert '"task"' not in block.split("    },\n    for name", 1)[0]
    specialist = (
        BACKEND / "app/services/agent_runtime/specialist_runtime.py"
    ).read_text(encoding="utf-8")
    assert "ProviderRuntimeRouter(db).route" in specialist
    assert "run_agent_with_db" not in specialist
    assert "create_task(" not in specialist
    assert "subprocess" not in specialist
    assert "shell" not in specialist.lower()


def test_event_and_checkpoint_evidence_remain_content_safe() -> None:
    events = (
        BACKEND / "app/services/agent_runtime/run_events.py"
    ).read_text(encoding="utf-8")
    checkpoints = (
        BACKEND / "app/services/agent_runtime/session_checkpoints.py"
    ).read_text(encoding="utf-8")
    for required in (
        '"prompt"',
        '"thought"',
        '"reasoning"',
        '"arguments"',
        '"raw_payload"',
        '"phone"',
        '"email"',
        '"address"',
        '"tracking_number"',
        '"waybill"',
    ):
        assert required in events
    for required in (
        '"message"',
        '"reply"',
        '"prompt"',
        '"argument"',
        '"result"',
        '"phone"',
        '"email"',
        '"address"',
        '"tracking"',
        '"waybill"',
    ):
        assert required in checkpoints


def test_canonical_architecture_document_exists() -> None:
    architecture = REPO / "docs/architecture/agent-runtime-operating-system.md"
    source = architecture.read_text(encoding="utf-8")
    assert "Status: **Canonical**" in source
    assert "No module may create a parallel" in source
    assert "Explicitly rejected architectures" in source
