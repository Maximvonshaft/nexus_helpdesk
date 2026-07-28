from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

TARGET_REVISION = "20260502_wc_cards"


def _run_backend(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout[-12000:]
    return completed


def test_webchat_cards_migration_upgrade_downgrade_upgrade(tmp_path: Path):
    """Exercise only the WebChat card/action migration contract.

    The repository-wide head, rollback and re-upgrade path is covered by the
    canonical PostgreSQL Acceptance job. This focused test must not inherit
    unrelated later migrations.
    """

    backend_dir = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "nexus_pr25_webchat_cards.db"
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "test",
            "TENANT_RUNTIME_AUTHORITY_MODE": "shadow",
            "DATABASE_URL": f"sqlite:///{db_path}",
            "EXPECTED_MIGRATION_HEAD": "",
        }
    )

    _run_backend(
        [sys.executable, "-m", "alembic", "upgrade", TARGET_REVISION],
        cwd=backend_dir,
        env=env,
    )

    engine = create_engine(env["DATABASE_URL"])
    insp = inspect(engine)
    try:
        tables = set(insp.get_table_names())
        assert "webchat_messages" in tables
        assert "webchat_card_actions" in tables

        message_cols = {
            col["name"] for col in insp.get_columns("webchat_messages")
        }
        assert {
            "message_type",
            "body_text",
            "payload_json",
            "metadata_json",
            "client_message_id",
            "delivery_status",
            "action_status",
        }.issubset(message_cols)

        action_cols = {
            col["name"] for col in insp.get_columns("webchat_card_actions")
        }
        assert {
            "id",
            "conversation_id",
            "ticket_id",
            "message_id",
            "action_id",
            "action_type",
            "action_payload_json",
            "submitted_by",
            "status",
            "created_at",
            "updated_at",
            "ip_hash",
            "user_agent_hash",
            "origin",
        }.issubset(action_cols)

        action_indexes = {
            idx["name"] for idx in insp.get_indexes("webchat_card_actions")
        }
        assert {
            "ix_webchat_card_actions_conversation_id",
            "ix_webchat_card_actions_ticket_id",
            "ix_webchat_card_actions_message_id",
            "ix_webchat_card_actions_action_id",
            "ix_webchat_card_actions_status",
            "uq_webchat_card_actions_once_per_action",
        }.issubset(action_indexes)
    finally:
        engine.dispose()

    _run_backend(
        [sys.executable, "-m", "alembic", "downgrade", "-1"],
        cwd=backend_dir,
        env=env,
    )
    _run_backend(
        [sys.executable, "-m", "alembic", "upgrade", TARGET_REVISION],
        cwd=backend_dir,
        env=env,
    )
