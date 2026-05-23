from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_FILES = [
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "participant_service.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "room_client.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "worker.py",
]
MIGRATIONS = ROOT / "backend" / "alembic" / "versions"
WEBAPP = ROOT / "webapp"
ARCH_DOC = ROOT / "docs" / "webcall-ai-agent-architecture.md"
ROLLOUT_DOC = ROOT / "docs" / "runbooks" / "webcall_ai_agent_rollout.md"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8").lower()


def test_pr8_runtime_files_do_not_import_media_network_or_provider_sdks():
    combined = "\n".join(_source(path) for path in RUNTIME_FILES)

    for forbidden in [
        "livekit",
        "requests",
        "httpx",
        "aiohttp",
        "websocket",
        "websockets",
        "speedaf",
        "openclaw",
        "openai",
        "codex",
        "provider_runtime",
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


def test_pr8_adds_no_migration_file():
    migration_names = [path.name.lower() for path in MIGRATIONS.glob("*.py")]

    assert not any("wcall_ai3" in name for name in migration_names)
    assert not any("participant_skeleton" in name for name in migration_names)
    assert not any("pr8" in name for name in migration_names)


def test_pr8_does_not_modify_frontend_contract_files():
    changed_frontend_markers = [path for path in WEBAPP.rglob("*") if path.is_file() and "webcall_ai_participant" in path.name]

    assert changed_frontend_markers == []


def test_docs_state_pr8_fake_participant_boundary():
    docs = (ARCH_DOC.read_text(encoding="utf-8") + "\n" + ROLLOUT_DOC.read_text(encoding="utf-8")).lower()

    assert "pr-8" in docs
    assert "fake livekit ai participant ownership skeleton" in docs
    assert "does not implement functional ai voice" in docs
    assert "does not join livekit media" in docs
    assert "does not expose ai participant tokens to browsers" in docs
