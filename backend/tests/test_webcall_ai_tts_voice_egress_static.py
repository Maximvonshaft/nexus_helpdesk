from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_FILES = [
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "tts_runtime.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "voice_egress_client.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "mock_turn_executor.py",
]
MIGRATIONS = ROOT / "backend" / "alembic" / "versions"
WEBAPP = ROOT / "webapp"


def _source(path: Path) -> str:
    source = path.read_text(encoding="utf-8").lower()
    source = source.replace("hello, this is speedaf ai support. please provide your tracking number.", "")
    source = source.replace("speedaf_tool_name", "")
    source = source.replace("speedaf.order.query", "")
    return source


def test_tts_voice_egress_has_no_forbidden_runtime_imports_or_tools():
    combined = "\n".join(_source(path) for path in RUNTIME_FILES)

    for forbidden in [
        "external_channel",
        "openai",
        "codex",
        "provider_runtime",
        "speedaf",
        "requests",
        "httpx",
        "aiohttp",
        "websocket",
        "websockets",
        "audiostream",
        "publish_track",
        "publish_data",
    ]:
        assert forbidden not in combined


def test_tts_voice_egress_does_not_persist_raw_audio_bytes():
    combined = "\n".join(_source(path) for path in RUNTIME_FILES)

    assert "largebinary" not in combined
    assert "audio_bytes" not in combined
    assert "raw audio" not in combined


def test_tts_voice_egress_adds_no_migration_file():
    migration_names = [path.name.lower() for path in MIGRATIONS.glob("*.py")]

    assert not any("wcall_ai3" in name for name in migration_names)
    assert not any("tts_runtime" in name for name in migration_names)
    assert not any("voice_egress" in name for name in migration_names)


def test_tts_voice_egress_does_not_touch_frontend_markers():
    markers = [path for path in WEBAPP.rglob("*") if path.is_file() and "webcall_ai_voice_egress" in path.name]

    assert markers == []
