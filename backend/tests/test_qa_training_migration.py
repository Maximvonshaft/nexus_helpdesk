from __future__ import annotations

from pathlib import Path


def test_qa_training_migration_declares_real_tables_and_indexes():
    backend_root = Path(__file__).resolve().parents[1]
    source = (backend_root / "alembic/versions/20260529_0039_qa_training_loop.py").read_text(encoding="utf-8")

    assert 'revision = "20260529_0039"' in source
    assert 'down_revision = "20260527_0038"' in source
    assert '"qa_reviews"' in source
    assert '"qa_training_tasks"' in source
    assert "sa.ForeignKey(\"tickets.id\")" in source
    assert "sa.ForeignKey(\"users.id\")" in source
    assert "ix_qa_reviews_ticket_created" in source
    assert "ix_qa_training_tasks_status_due" in source
    assert "Base.metadata.create_all" not in source
