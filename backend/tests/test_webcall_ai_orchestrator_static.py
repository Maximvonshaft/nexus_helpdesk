from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_FILES = [
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "orchestrator.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "reply_builder.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "mock_turn_executor.py",
]
MIGRATIONS = ROOT / "backend" / "alembic" / "versions"
WEBAPP = ROOT / "webapp"


def _source(path: Path) -> str:
    source = path.read_text(encoding="utf-8").lower()
    source = source.replace("hello, this is speedaf ai support. please provide your tracking number.", "")
    source = source.replace("speedaf_tool_name", "")
    source = source.replace("speedaf.order.query", "")
    source = source.replace("tracking_fact_service", "")
    return source


def test_orchestrator_has_no_llm_or_provider_runtime_imports():
    combined = "\n".join(_source(path) for path in RUNTIME_FILES)

    for forbidden in ["external_channel", "openai", "codex", "provider_runtime", "llm"]:
        assert forbidden not in combined


def test_orchestrator_cannot_produce_speedaf_write_tool_names():
    combined = "\n".join(_source(path) for path in RUNTIME_FILES)

    for forbidden in [
        "speedaf.order.cancel",
        "speedaf.order.update_address",
        "speedaf.work_order.create",
        "work_order.create",
        "cancel_order",
        "submit_address_update_directly",
    ]:
        assert forbidden not in combined


def test_orchestrator_does_not_generate_or_publish_audio():
    combined = "\n".join(_source(path) for path in RUNTIME_FILES)

    for forbidden in ["publish_track", "publish_data", "audiostream", "microphone", "tts audio"]:
        assert forbidden not in combined


def test_orchestrator_adds_no_migration_file():
    migration_names = [path.name.lower() for path in MIGRATIONS.glob("*.py")]

    assert not any("wcall_ai3" in name for name in migration_names)
    assert not any("orchestrator" in name for name in migration_names)


def test_orchestrator_does_not_touch_frontend_markers():
    markers = [path for path in WEBAPP.rglob("*") if path.is_file() and "webcall_ai_orchestrator" in path.name]

    assert markers == []
