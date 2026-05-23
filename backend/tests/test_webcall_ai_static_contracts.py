from pathlib import Path

from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

from app.voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceSession

ROOT = Path(__file__).resolve().parents[2]
ARCH_DOC = ROOT / "docs" / "webcall-ai-agent-architecture.md"
ROLLOUT_DOC = ROOT / "docs" / "runbooks" / "webcall_ai_agent_rollout.md"
CONFIG = ROOT / "backend" / "app" / "services" / "webcall_ai" / "config.py"
MIGRATION = ROOT / "backend" / "alembic" / "versions" / "20260523_wcall_ai1_webcall_ai_agent_foundation.py"


def _docs() -> str:
    return (ARCH_DOC.read_text(encoding="utf-8") + "\n" + ROLLOUT_DOC.read_text(encoding="utf-8")).lower()


def test_docs_state_livekit_participant_and_backend_worker_boundary():
    docs = _docs()

    assert "livekit room" in docs
    assert "ai participant" in docs
    assert "backend ai worker" in docs or "backend worker" in docs


def test_docs_state_browser_and_llm_secret_action_boundaries():
    docs = _docs()

    assert "browser code must never receive" in docs
    assert "ai provider tokens" in docs
    assert "speedaf appcode" in docs
    assert "llm must never directly execute speedaf write actions" in docs
    assert "speedaf.order.cancel" in docs
    assert "speedaf.order.update_address" in docs
    assert "speedaf.work_order.create" in docs


def test_docs_state_handoff_categories_and_non_functional_claim():
    docs = _docs()

    for marker in ["cancel", "address", "compensation", "driver or dsp responsibility"]:
        assert marker in docs
    assert "pr-0/pr-1 does not make webcall ai functional yet" in docs
    assert "does not implement real stt" in docs
    assert "does not implement real tts" in docs


def test_docs_state_rollback_by_feature_flags():
    docs = _docs()

    assert "rollback" in docs
    assert "feature flags" in docs or "flags off" in docs
    assert "leave the new tables dormant" in docs or "leave tables dormant" in docs
    assert "do not drop" in docs


def test_config_defaults_are_static_false_and_mock():
    config = CONFIG.read_text(encoding="utf-8")

    assert '_env_bool("WEBCALL_AI_AGENT_ENABLED", False)' in config
    assert 'os.getenv("WEBCALL_STT_PROVIDER", "mock")' in config
    assert 'os.getenv("WEBCALL_TTS_PROVIDER", "mock")' in config
    assert 'os.getenv("WEBCALL_AI_PROVIDER", "provider_runtime")' in config
    assert '_env_bool("WEBCALL_AI_ALLOW_SPEEDAF_WORK_ORDER", False)' in config
    assert '_env_bool("WEBCALL_AI_ALLOW_CANCEL", False)' in config
    assert '_env_bool("WEBCALL_AI_ALLOW_ADDRESS_UPDATE", False)' in config
    assert '_env_bool("WEBCALL_AI_RECORD_RAW_AUDIO", False)' in config


def test_voice_models_include_ai_session_fields_and_unique_turn_constraint():
    session_columns = WebchatVoiceSession.__table__.columns

    for column in [
        "ai_agent_status",
        "ai_agent_started_at",
        "ai_agent_ended_at",
        "ai_handoff_reason",
        "ai_language",
        "ai_turn_count",
    ]:
        assert column in session_columns

    constraints = WebchatVoiceAITurn.__table__.constraints
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_voice_ai_turn_session_index"
        and {column.name for column in constraint.columns} == {"voice_session_id", "turn_index"}
        for constraint in constraints
    )


def test_tool_call_log_id_is_non_fk_audit_reference():
    column = WebchatVoiceAIAction.__table__.columns["tool_call_log_id"]
    fk_columns = {
        element.parent.name
        for constraint in WebchatVoiceAIAction.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        for element in constraint.elements
    }
    docs = _docs()

    assert column.index is True
    assert "tool_call_log_id" not in fk_columns
    assert "without a foreign key" in docs
    assert "low-coupling" in docs


def test_migration_contains_ai_tables_fields_and_unique_constraint():
    migration = MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "20260523_wcall_ai1"' in migration
    assert 'down_revision = "20260522_0031"' in migration
    assert "webchat_voice_ai_turns" in migration
    assert "webchat_voice_ai_actions" in migration
    assert "ai_agent_status" in migration
    assert "ai_turn_count" in migration
    assert "uq_voice_ai_turn_session_index" in migration
    assert 'sa.Column("tool_call_log_id", sa.Integer(), nullable=True)' in migration
    assert 'sa.ForeignKey("tool_call_logs.id")' not in migration
