from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_FILES = [
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "room_client.py",
    ROOT / "backend" / "app" / "services" / "webcall_ai" / "worker.py",
]
MIGRATIONS = ROOT / "backend" / "alembic" / "versions"
WEBAPP = ROOT / "webapp"
ARCH_DOC = ROOT / "docs" / "webcall-ai-agent-architecture.md"
ROLLOUT_DOC = ROOT / "docs" / "runbooks" / "webcall_ai_agent_rollout.md"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8").lower()


def test_pr9_runtime_files_do_not_import_livekit_media_or_network_clients():
    combined = "\n".join(_source(path) for path in RUNTIME_FILES)

    for forbidden in [
        "from livekit ",
        "from livekit.",
        "\nimport livekit",
        "livekit.rtc",
        "livekit.agents",
        "livekit.api",
        "requests",
        "httpx",
        "aiohttp",
        "websocket",
        "websockets",
        "speedaf",
        "external_channel",
        "openai",
        "legacy_ai_provider",
        "provider_runtime",
    ]:
        assert forbidden not in combined


def test_pr9_adds_no_migration_file():
    migration_names = [path.name.lower() for path in MIGRATIONS.glob("*.py")]

    assert not any("wcall_ai3" in name for name in migration_names)
    assert not any("token_issuer" in name for name in migration_names)
    assert not any("pr9" in name for name in migration_names)


def test_pr9_does_not_add_frontend_token_exposure_files():
    frontend_hits = [path for path in WEBAPP.rglob("*") if path.is_file() and "participant_token" in path.name.lower()]

    assert frontend_hits == []


def test_docs_state_pr9_token_issuer_boundary():
    docs = (ARCH_DOC.read_text(encoding="utf-8") + "\n" + ROLLOUT_DOC.read_text(encoding="utf-8")).lower()

    assert "pr-9" in docs
    assert "server-side livekit ai participant token issuer wrapper" in docs
    assert "does not implement functional ai voice" in docs
    assert "does not join livekit media" in docs
    assert "does not expose ai participant tokens to browsers" in docs
