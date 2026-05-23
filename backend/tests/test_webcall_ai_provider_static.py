from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_FILES = [
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "media_schemas.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "stt_provider.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "tts_provider.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "mock_media_provider.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "contract_stub_provider.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "provider_router.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "mock_turn_executor.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "worker.py",
    ROOT / "backend" / "scripts" / "run_webcall_ai_worker.py",
]
MIGRATIONS = ROOT / "backend" / "alembic" / "versions"
ARCH_DOC = ROOT / "docs" / "webcall-ai-agent-architecture.md"
ROLLOUT_DOC = ROOT / "docs" / "runbooks" / "webcall_ai_agent_rollout.md"


def _scan_source(path: Path) -> str:
    source = path.read_text(encoding="utf-8").lower()
    source = source.replace("hello, this is speedaf ai support. please provide your tracking number.", "")
    source = source.replace("speedaf_tool_name", "")
    return source


def test_pr5_runtime_files_do_not_import_real_media_network_or_provider_sdks():
    combined = "\n".join(_scan_source(path) for path in RUNTIME_FILES)

    for forbidden in [
        "livekit",
        "httpx",
        "requests",
        "urllib",
        "aiohttp",
        "websocket",
        "websockets",
        "openclaw",
        "openai",
        "codex",
        "provider_runtime",
        "speedaf",
        "sounddevice",
        "pyaudio",
        "whisper",
        "boto3",
        "google.cloud",
        "azure.",
        "ffmpeg",
    ]:
        assert forbidden not in combined
    assert "import av" not in combined
    assert "from av" not in combined


def test_pr5_adds_no_migration_file():
    migration_names = [path.name.lower() for path in MIGRATIONS.glob("*.py")]

    assert not any("wcall_ai3" in name for name in migration_names)
    assert not any("provider_contract" in name for name in migration_names)
    assert not any("pr5" in name for name in migration_names)


def test_docs_state_pr5_is_contract_skeleton_only():
    docs = (ARCH_DOC.read_text(encoding="utf-8") + "\n" + ROLLOUT_DOC.read_text(encoding="utf-8")).lower()

    assert "pr-5" in docs
    assert "real stt/tts provider contract skeleton" in docs
    assert "does not implement real stt/tts" in docs
    assert "does not implement functional ai voice" in docs
    assert "does not join livekit" in docs
    assert "does not read/publish real audio" in docs
    assert "does not import real provider sdks" in docs
    assert "does not perform external network calls" in docs
    assert "pr-6" in docs
