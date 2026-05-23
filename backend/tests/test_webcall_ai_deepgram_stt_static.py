from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEEPGRAM_PROVIDER = ROOT / "backend" / "app" / "services" / "webcall_ai" / "deepgram_stt_provider.py"
RUNTIME_FILES = [
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "config.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "provider_router.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "mock_turn_executor.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "worker.py",
    ROOT / "backend" / "scripts" / "run_webcall_ai_worker.py",
]
MIGRATIONS = ROOT / "backend" / "alembic" / "versions"
ARCH_DOC = ROOT / "docs" / "webcall-ai-agent-architecture.md"
ROLLOUT_DOC = ROOT / "docs" / "runbooks" / "webcall_ai_agent_rollout.md"


def _source(path: Path) -> str:
    source = path.read_text(encoding="utf-8").lower()
    source = source.replace("hello, this is speedaf ai support. please provide your tracking number.", "")
    source = source.replace("speedaf_tool_name", "")
    source = source.replace('"provider_runtime"', "")
    source = source.replace("provider_runtime in this foundation pr", "")
    source = source.replace("allow_speedaf_work_order", "")
    source = source.replace("webcall_ai_allow_speedaf_work_order", "")
    return source


def test_urllib_imports_are_scoped_to_deepgram_provider_file():
    for path in RUNTIME_FILES:
        assert "urllib" not in _source(path)

    deepgram = _source(DEEPGRAM_PROVIDER)
    assert "import urllib.error" in deepgram
    assert "import urllib.request" in deepgram


def test_pr6_runtime_files_do_not_import_forbidden_sdks_or_media_stacks():
    combined = "\n".join(_source(path) for path in RUNTIME_FILES + [DEEPGRAM_PROVIDER])

    for forbidden in [
        "requests",
        "httpx",
        "aiohttp",
        "websocket",
        "websockets",
        "livekit",
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


def test_pr6_adds_no_migration_file():
    migration_names = [path.name.lower() for path in MIGRATIONS.glob("*.py")]

    assert not any("deepgram" in name for name in migration_names)
    assert not any("wcall_ai3" in name for name in migration_names)
    assert not any("pr6" in name for name in migration_names)


def test_docs_state_pr6_deepgram_stt_adapter_boundaries():
    docs = (ARCH_DOC.read_text(encoding="utf-8") + "\n" + ROLLOUT_DOC.read_text(encoding="utf-8")).lower()

    assert "pr-6" in docs
    assert "deepgram stt adapter" in docs
    assert "does not implement functional ai voice" in docs
    assert "does not join livekit" in docs
    assert "does not read/publish webrtc audio" in docs
    assert "does not enable real stt by default" in docs
