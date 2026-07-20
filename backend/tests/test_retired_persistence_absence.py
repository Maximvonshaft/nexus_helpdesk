from pathlib import Path

from app.main import app
from app.models import Base

ROOT = Path(__file__).resolve().parents[2]
RETIRED_TABLES = {
    "external_channel_conversation_links",
    "external_channel_transcript_messages",
    "external_channel_attachment_references",
    "external_channel_sync_cursors",
    "external_channel_unresolved_events",
}


def test_runtime_metadata_has_no_retired_tables_or_columns():
    assert RETIRED_TABLES.isdisjoint(Base.metadata.tables)
    assert "unresolved_event_id" not in Base.metadata.tables["operator_tasks"].c
    assert "external_channel_account_id" not in Base.metadata.tables["channel_onboarding_tasks"].c


def test_application_has_no_retired_routes_or_runtime_modules():
    paths = {getattr(route, "path", "") for route in app.routes if getattr(route, "path", None)}
    assert all("external_channel" not in path for path in paths)
    runtime_root = ROOT / "backend/app"
    offenders = []
    for candidate in runtime_root.rglob("*.py"):
        text = candidate.read_text(encoding="utf-8")
        if "external_channel" in text.lower() or "ExternalChannel" in text:
            offenders.append(candidate.relative_to(ROOT).as_posix())
    assert offenders == []
