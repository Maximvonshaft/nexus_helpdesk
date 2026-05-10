from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/db_query_timing_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.services.observability import record_db_query, sql_statement_category  # noqa: E402


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
