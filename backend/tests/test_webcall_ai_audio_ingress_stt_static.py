from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_FILES = [
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "stt_runtime.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "transcript_writer.py",
]
WORKER_FILES = [
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "mock_turn_executor.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "worker.py",
]
MIGRATIONS = ROOT / "backend" / "alembic" / "versions"
WEBAPP = ROOT / "webapp"


def _source(path: Path) -> str:
    source = path.read_text(encoding="utf-8").lower()
    source = source.replace("hello, this is speedaf ai support. please provide your tracking number.", "")
    source = source.replace("speedaf_tool_name", "")
    return source


def test_audio_ingress_stt_runtime_has_no_forbidden_provider_or_tool_imports():
    combined = "\n".join(_source(path) for path in RUNTIME_FILES + WORKER_FILES)

    for forbidden in [
        "speedaf",
        "openclaw",
        "openai",
        "codex",
        "provider_runtime",
        "publish_track",
        "publish_data",
        "audiostream",
        "microphone",
        "sounddevice",
        "pyaudio",
        "ffmpeg",
        "import av",
        "from av",
    ]:
        assert forbidden not in combined


def test_audio_ingress_stt_runtime_adds_no_network_client_imports():
    combined = "\n".join(_source(path) for path in RUNTIME_FILES)

    for forbidden in ["requests", "httpx", "aiohttp", "websocket", "websockets", "urllib", "livekit"]:
        assert forbidden not in combined


def test_audio_ingress_stt_adds_no_migration_file():
    migration_names = [path.name.lower() for path in MIGRATIONS.glob("*.py")]

    assert not any("wcall_ai3" in name for name in migration_names)
    assert not any("stt_runtime" in name for name in migration_names)
    assert not any("audio_ingress" in name for name in migration_names)


def test_audio_ingress_stt_does_not_touch_frontend_markers():
    markers = [path for path in WEBAPP.rglob("*") if path.is_file() and "webcall_ai_stt_runtime" in path.name]

    assert markers == []
