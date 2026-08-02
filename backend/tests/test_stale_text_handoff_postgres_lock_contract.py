from __future__ import annotations

import os

from sqlalchemy import create_engine, create_mock_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/stale-text-handoff-postgres-lock-contract.db",
)

from app.models_agent_routing import OperatorAgentState
from app.services.stale_text_handoff_reconciliation import _lock_candidate_rows
from app.webchat_models import WebchatConversation, WebchatHandoffRequest


def _candidate_query(session: Session):
    return (
        session.query(WebchatHandoffRequest, WebchatConversation)
        .join(
            WebchatConversation,
            WebchatConversation.id == WebchatHandoffRequest.conversation_id,
        )
        .outerjoin(
            OperatorAgentState,
            OperatorAgentState.user_id
            == WebchatHandoffRequest.assigned_agent_id,
        )
    )


def test_postgresql_lock_targets_only_non_nullable_candidate_rows():
    engine = create_mock_engine("postgresql+psycopg://", lambda *_args, **_kwargs: None)
    session = Session(bind=engine)
    try:
        query = _lock_candidate_rows(session, _candidate_query(session))
        sql = " ".join(
            str(query.statement.compile(dialect=postgresql.dialect())).split()
        )
    finally:
        session.close()

    assert "LEFT OUTER JOIN operator_agent_states" in sql
    assert (
        "FOR UPDATE OF webchat_handoff_requests, webchat_conversations SKIP LOCKED"
        in sql
    )
    assert "FOR UPDATE OF operator_agent_states" not in sql


def test_non_postgresql_query_does_not_add_row_lock():
    engine = create_engine("sqlite://", future=True)
    session = Session(bind=engine)
    try:
        query = _lock_candidate_rows(session, _candidate_query(session))
        sql = " ".join(str(query.statement).split())
    finally:
        session.close()
        engine.dispose()

    assert "FOR UPDATE" not in sql
