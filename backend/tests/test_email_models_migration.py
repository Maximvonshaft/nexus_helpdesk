from __future__ import annotations

from sqlalchemy import inspect

from email_test_utils import make_session


def test_email_models_create_expected_tables(tmp_path):
    engine, db = make_session(tmp_path)
    try:
        tables = set(inspect(engine).get_table_names())
        assert {"email_channel_accounts", "email_outbound_metadata", "email_delivery_events", "email_inbound_messages", "email_suppressions", "email_webhook_replays"} <= tables
    finally:
        db.close()
        engine.dispose()
