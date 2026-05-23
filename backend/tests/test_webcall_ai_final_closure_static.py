from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_FILES = [
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "pilot_canary_gate.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "pilot_session_source.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "pilot_fake_tracking.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "pilot_closure.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "handoff_service.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "evidence_builder.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "real_media_smoke.py",
    ROOT / "backend" / "scripts" / "run_webcall_ai_pilot_closure_smoke.py",
]
MIGRATIONS = ROOT / "backend" / "alembic" / "versions"
WEBAPP = ROOT / "webapp"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8").lower()


def test_final_closure_runtime_has_no_forbidden_network_or_provider_calls():
    combined = "\n".join(_source(path) for path in RUNTIME_FILES)

    for forbidden in [
        "requests",
        "httpx",
        "aiohttp",
        "websocket",
        "websockets",
        "openclaw",
        "openai",
        "codex",
        "provider_runtime",
        "workorder",
        "work_order",
        "cancel_order",
        "update_address",
        "refund",
        "compensation",
        "publish_track",
    ]:
        assert forbidden not in combined


def test_final_closure_does_not_persist_tokens_or_raw_audio():
    combined = "\n".join(_source(path) for path in RUNTIME_FILES)

    assert "token_file" not in combined
    assert "access_token" not in combined
    assert "largebinary" not in combined
    assert "audio_bytes" not in combined
    assert "raw_audio" not in combined
    assert "provider payload" not in combined


def test_final_closure_adds_no_migration_file():
    migration_names = [path.name.lower() for path in MIGRATIONS.glob("*.py")]

    assert not any("pilot" in name for name in migration_names)
    assert not any("wcall_ai3" in name for name in migration_names)


def test_final_closure_does_not_touch_frontend_markers():
    markers = [path for path in WEBAPP.rglob("*") if path.is_file() and "pilot_closure" in path.name]

    assert markers == []
