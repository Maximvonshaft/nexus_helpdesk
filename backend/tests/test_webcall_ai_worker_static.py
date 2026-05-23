from pathlib import Path

from app.voice_models import WebchatVoiceSession

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend" / "scripts" / "run_webcall_ai_worker.py"
LIFECYCLE = ROOT / "backend" / "app" / "services" / "webcall_ai" / "lifecycle.py"
MIGRATION = ROOT / "backend" / "alembic" / "versions" / "20260523_wcall_ai2_webcall_ai_worker_claim_lifecycle.py"
ARCH_DOC = ROOT / "docs" / "webcall-ai-agent-architecture.md"
ROLLOUT_DOC = ROOT / "docs" / "runbooks" / "webcall_ai_agent_rollout.md"


def test_worker_script_exists_and_supports_required_flags():
    script = SCRIPT.read_text(encoding="utf-8")

    assert SCRIPT.exists()
    assert "--once" in script
    assert "--worker-id" in script
    assert "--limit" in script
    assert "--lease-seconds" in script
    assert "claimed=" in script
    assert "released=" in script


def test_worker_script_has_no_forbidden_runtime_integrations():
    script = SCRIPT.read_text(encoding="utf-8").lower()

    for forbidden in [
        "livekit",
        "stt",
        "tts",
        "speedaf",
        "openclaw",
        "provider_runtime",
        "llm",
        "openai",
        "codex",
    ]:
        assert forbidden not in script


def test_lifecycle_uses_claim_statuses_without_media_states():
    lifecycle = LIFECYCLE.read_text(encoding="utf-8")

    for status in ["pending", "claimed", "released", "failed", "skipped"]:
        assert status in lifecycle
    for media_status in ["speaking", "listening", "joined"]:
        assert media_status not in lifecycle


def test_model_and_migration_include_worker_claim_fields():
    columns = WebchatVoiceSession.__table__.columns
    migration = MIGRATION.read_text(encoding="utf-8")

    for column in [
        "ai_agent_worker_id",
        "ai_agent_claimed_at",
        "ai_agent_lease_expires_at",
        "ai_agent_last_heartbeat_at",
        "ai_agent_error_code",
        "ai_agent_error_message",
    ]:
        assert column in columns
        assert column in migration

    assert 'revision = "20260523_wcall_ai2"' in migration
    assert 'down_revision = "20260523_wcall_ai1"' in migration


def test_docs_state_pr2_is_noop_claim_lifecycle_only():
    docs = (ARCH_DOC.read_text(encoding="utf-8") + "\n" + ROLLOUT_DOC.read_text(encoding="utf-8")).lower()

    assert "pr-2" in docs
    assert "no-op claim lifecycle only" in docs
    assert "does not implement functional ai voice" in docs
    assert "does not connect media, stt, tts, llm, or speedaf" in docs
