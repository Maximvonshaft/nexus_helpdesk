from __future__ import annotations

from app.services.observability import record_db_query, sql_statement_category


def test_sql_statement_category_is_low_cardinality() -> None:
    assert sql_statement_category("SELECT * FROM tickets WHERE id = %s") == "select"
    assert sql_statement_category("update tickets set title = %s") == "update"
    assert sql_statement_category("/* comment */ DELETE FROM tickets") == "delete"
    assert sql_statement_category("CREATE INDEX ix_demo ON demo(id)") == "ddl"
    assert sql_statement_category("VACUUM") == "other"


def test_record_db_query_never_requires_raw_sql_parameters() -> None:
    # The statement is categorized internally; parameters are intentionally not
    # part of the API so raw SQL values cannot become metric labels.
    record_db_query(1.5, "SELECT * FROM tickets WHERE id = %s", slow_threshold_ms=500, request_id="rid-test")
    record_db_query(501, "UPDATE tickets SET title = %s", slow_threshold_ms=500, request_id="rid-test")
