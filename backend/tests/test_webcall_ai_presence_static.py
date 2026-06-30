from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRESENCE_CLIENT = ROOT / "backend" / "app" / "services" / "webcall_ai" / "presence_client.py"
WORKER = ROOT / "backend" / "app" / "services" / "webcall_ai" / "worker.py"
MIGRATIONS = ROOT / "backend" / "alembic" / "versions"
WEBAPP = ROOT / "webapp"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8").lower()


def test_presence_runtime_forbids_audio_publish_subscribe_symbols():
    combined = _source(PRESENCE_CLIENT)

    for forbidden in [
        "audiostream",
        "publish_track",
        "publish_data",
        "subscribe",
        "track_subscribed",
        "microphone",
        "sounddevice",
        "pyaudio",
        "ffmpeg",
        "import av",
        "from av",
        "requests",
        "httpx",
        "aiohttp",
        "websocket",
        "websockets",
    ]:
        assert forbidden not in combined


def test_presence_runtime_has_no_speedaf_or_llm_provider_imports():
    combined = _source(PRESENCE_CLIENT) + "\n" + _source(WORKER)

    for forbidden in ["speedaf", "external_channel", "openai", "codex", "provider_runtime"]:
        assert forbidden not in combined


def test_presence_runtime_uses_lazy_livekit_import_only():
    source = _source(PRESENCE_CLIENT)

    assert "from livekit" not in source
    assert "\nimport livekit" not in source
    assert 'import_module("livekit.rtc")' in source


def test_presence_adds_no_migration_file():
    migration_names = [path.name.lower() for path in MIGRATIONS.glob("*.py")]

    assert not any("wcall_ai3" in name for name in migration_names)
    assert not any("presence" in name for name in migration_names)


def test_presence_does_not_touch_frontend_markers():
    markers = [path for path in WEBAPP.rglob("*") if path.is_file() and "webcall_ai_presence" in path.name]

    assert markers == []
