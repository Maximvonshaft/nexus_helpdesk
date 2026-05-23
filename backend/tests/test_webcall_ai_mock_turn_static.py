from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXECUTOR = ROOT / "backend" / "app" / "services" / "webcall_ai" / "mock_turn_executor.py"
WORKER = ROOT / "backend" / "app" / "services" / "webcall_ai" / "worker.py"
ARCH_DOC = ROOT / "docs" / "webcall-ai-agent-architecture.md"
ROLLOUT_DOC = ROOT / "docs" / "runbooks" / "webcall_ai_agent_rollout.md"


def _runtime_lines(path: Path) -> list[str]:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip().lower()
        if stripped.startswith(("import ", "from ")) or "(" in stripped:
            lines.append(stripped)
    return lines


def test_static_no_real_media_or_external_provider_imports():
    combined = "\n".join(_runtime_lines(EXECUTOR) + _runtime_lines(WORKER))

    forbidden_tokens = [
        "import livekit",
        "from livekit",
        "import requests",
        "from requests",
        "import httpx",
        "from httpx",
        "import urllib",
        "from urllib",
        "provider_runtime",
        "openclaw",
        "openai",
        "codex",
        "llm",
        "speedaf.",
    ]
    for token in forbidden_tokens:
        assert token not in combined


def test_mock_executor_contains_deterministic_safe_contract():
    source = EXECUTOR.read_text(encoding="utf-8")

    assert "execute_mock_turn_for_claimed_session" in source
    assert "Hello, this is Speedaf AI support. Please provide your tracking number." in source
    assert 'provider="mock"' in source
    assert "stt_provider=stt_provider_name" in source
    assert 'tts_provider="mock"' in source
    assert 'latency_ms=0' in source
    assert 'nexus_decision="allowed"' in source
    assert 'result_status=MOCK_RESULT_STATUS' in source
    assert 'speedaf_tool_name=None' in source
    assert 'background_job_id=None' in source
    assert 'tool_call_log_id=None' in source


def test_docs_state_pr3_and_pr4_are_deterministic_mock_only():
    docs = (ARCH_DOC.read_text(encoding="utf-8") + "\n" + ROLLOUT_DOC.read_text(encoding="utf-8")).lower()

    assert "pr-3" in docs
    assert "deterministic mock turn" in docs
    assert "pr-4" in docs
    assert "deterministic mock stt/tts boundaries" in docs
    assert "does not make webcall ai functional" in docs
    assert "does not read audio" in docs or "no audio" in docs
    assert "no audio, stt, tts, llm, openclaw, or speedaf calls" in docs
