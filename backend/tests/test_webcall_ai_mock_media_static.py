from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_FILES = [
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "media_schemas.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "stt_provider.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "tts_provider.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "mock_media_provider.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "mock_turn_executor.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "worker.py",
    ROOT / "backend" / "scripts" / "run_webcall_ai_worker.py",
]
MIGRATIONS = ROOT / "backend" / "alembic" / "versions"
ARCH_DOC = ROOT / "docs" / "webcall-ai-agent-architecture.md"
ROLLOUT_DOC = ROOT / "docs" / "runbooks" / "webcall_ai_agent_rollout.md"
RUNNER = ROOT / "backend" / "scripts" / "run_webcall_ai_worker.py"


def _scan_source(path: Path) -> str:
    source = path.read_text(encoding="utf-8").lower()
    source = source.replace("hello, this is speedaf ai support. please provide your tracking number.", "")
    source = source.replace("speedaf_tool_name", "")
    return source


def test_pr4_runtime_files_do_not_reference_real_media_or_provider_integrations():
    combined = "\n".join(_scan_source(path) for path in RUNTIME_FILES)

    for forbidden in [
        "livekit",
        "httpx",
        "requests",
        "urllib",
        "external_channel",
        "openai",
        "legacy_ai_provider",
        "provider_runtime",
        "speedaf",
        "websocket",
        "sounddevice",
        "pyaudio",
        "whisper",
        "boto3",
        "google.cloud",
        "azure.",
    ]:
        assert forbidden not in combined


def test_pr4_adds_no_mock_media_migration_file():
    migration_names = [path.name.lower() for path in MIGRATIONS.glob("*.py")]

    assert not any("wcall_ai3" in name for name in migration_names)
    assert not any("mock_media" in name for name in migration_names)
    assert not any("pr4" in name for name in migration_names)


def test_docs_state_pr4_is_mock_media_boundary_only():
    docs = (ARCH_DOC.read_text(encoding="utf-8") + "\n" + ROLLOUT_DOC.read_text(encoding="utf-8")).lower()

    assert "pr-4" in docs
    assert "deterministic mock stt/tts boundaries" in docs
    assert "does not implement functional ai voice" in docs
    assert "does not read audio" in docs
    assert "does not join livekit media" in docs
    assert "does not connect real stt/tts" in docs
    assert "does not call llm/provider runtime" in docs
    assert "does not call external_channel" in docs
    assert "does not call speedaf" in docs
    assert "does not change frontend" in docs
    assert "future real providers must implement the provider interfaces" in docs
    assert "behind feature flags" in docs


def test_runner_output_includes_mock_media_counters():
    runner = RUNNER.read_text(encoding="utf-8")

    assert "stt_events=" in runner
    assert "tts_events=" in runner
