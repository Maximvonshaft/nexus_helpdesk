#!/usr/bin/env python3
"""Read-only PostgreSQL connection-budget and hot-query qualification.

The report intentionally excludes SQL text, parameters, connection strings and
customer values. Query hotspots are identified only by PostgreSQL queryid.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text


@dataclass(frozen=True)
class ProcessPool:
    role: str
    process_count: int
    pool_size: int
    max_overflow: int

    @property
    def maximum_connections(self) -> int:
        return self.process_count * (self.pool_size + self.max_overflow)


def parse_process_pool(value: str) -> ProcessPool:
    parts = [item.strip() for item in value.split(":")]
    if len(parts) != 4 or not parts[0]:
        raise ValueError("process_pool_format_invalid")
    try:
        process_count, pool_size, max_overflow = (int(item) for item in parts[1:])
    except ValueError as exc:
        raise ValueError("process_pool_value_invalid") from exc
    if not 1 <= process_count <= 100:
        raise ValueError("process_count_out_of_range")
    if not 1 <= pool_size <= 50:
        raise ValueError("pool_size_out_of_range")
    if not 0 <= max_overflow <= 50:
        raise ValueError("max_overflow_out_of_range")
    return ProcessPool(parts[0][:80], process_count, pool_size, max_overflow)


def calculate_connection_budget(
    pools: list[ProcessPool],
    *,
    database_max_connections: int,
    reserved_connections: int,
    maximum_budget_percent: int = 70,
) -> dict[str, Any]:
    if database_max_connections <= 0:
        raise ValueError("database_max_connections_invalid")
    if not 0 <= reserved_connections < database_max_connections:
        raise ValueError("reserved_connections_invalid")
    if not 1 <= maximum_budget_percent <= 95:
        raise ValueError("maximum_budget_percent_invalid")

    configured = sum(pool.maximum_connections for pool in pools)
    usable = database_max_connections - reserved_connections
    allowed = int(database_max_connections * maximum_budget_percent / 100)
    remaining = database_max_connections - configured
    return {
        "configured_application_max_connections": configured,
        "database_max_connections": database_max_connections,
        "reserved_connections": reserved_connections,
        "usable_connections": usable,
        "maximum_budget_percent": maximum_budget_percent,
        "allowed_application_connections": allowed,
        "remaining_connections_at_configured_max": remaining,
        "budget_percent": round(configured * 100 / database_max_connections, 2),
        "within_budget": configured <= allowed and configured <= usable,
        "process_pools": [
            {
                **asdict(pool),
                "maximum_connections": pool.maximum_connections,
            }
            for pool in pools
        ],
    }


def _scalar(connection, statement: str, default: int = 0) -> int:
    try:
        value = connection.execute(text(statement)).scalar_one()
        return int(value)
    except Exception:
        return default


def collect_postgresql_snapshot(database_url: str, *, top_queries: int = 20) -> dict[str, Any]:
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise ValueError("postgresql_database_url_required")
    engine = create_engine(
        database_url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=5,
        pool_pre_ping=True,
        future=True,
    )
    try:
        with engine.connect() as connection:
            max_connections = _scalar(
                connection,
                "SELECT current_setting('max_connections')::int",
            )
            reserved = _scalar(
                connection,
                "SELECT current_setting('superuser_reserved_connections')::int",
            )
            connection_rows = connection.execute(
                text(
                    """
                    SELECT COALESCE(state, 'unknown') AS state, count(*)::int AS count
                    FROM pg_stat_activity
                    WHERE datname = current_database()
                    GROUP BY COALESCE(state, 'unknown')
                    ORDER BY state
                    """
                )
            ).mappings().all()
            long_transactions = connection.execute(
                text(
                    """
                    SELECT count(*)::int
                    FROM pg_stat_activity
                    WHERE datname = current_database()
                      AND xact_start IS NOT NULL
                      AND clock_timestamp() - xact_start > interval '60 seconds'
                    """
                )
            ).scalar_one()
            waiting_connections = connection.execute(
                text(
                    """
                    SELECT count(*)::int
                    FROM pg_stat_activity
                    WHERE datname = current_database()
                      AND wait_event IS NOT NULL
                    """
                )
            ).scalar_one()
            pg_stat_statements_available = bool(
                connection.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements')"
                    )
                ).scalar_one()
            )
            hotspots: list[dict[str, Any]] = []
            if pg_stat_statements_available:
                hotspots = [
                    dict(row)
                    for row in connection.execute(
                        text(
                            """
                            SELECT queryid::text AS queryid,
                                   calls::bigint AS calls,
                                   round(total_exec_time::numeric, 3) AS total_exec_time_ms,
                                   round(mean_exec_time::numeric, 3) AS mean_exec_time_ms,
                                   rows::bigint AS rows,
                                   shared_blks_hit::bigint AS shared_blks_hit,
                                   shared_blks_read::bigint AS shared_blks_read,
                                   temp_blks_read::bigint AS temp_blks_read,
                                   temp_blks_written::bigint AS temp_blks_written,
                                   wal_bytes::text AS wal_bytes
                            FROM pg_stat_statements
                            WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
                            ORDER BY total_exec_time DESC
                            LIMIT :limit
                            """
                        ),
                        {"limit": max(1, min(top_queries, 100))},
                    ).mappings().all()
                ]
            return {
                "database_max_connections": max_connections,
                "reserved_connections": reserved,
                "connections_by_state": [dict(row) for row in connection_rows],
                "long_transactions_over_60s": int(long_transactions),
                "waiting_connections": int(waiting_connections),
                "pg_stat_statements_available": pg_stat_statements_available,
                "hot_queries_by_total_time": hotspots,
                "query_text_included": False,
            }
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--process-pool",
        action="append",
        default=[],
        metavar="ROLE:COUNT:POOL_SIZE:MAX_OVERFLOW",
    )
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--database-max-connections", type=int)
    parser.add_argument("--reserved-connections", type=int)
    parser.add_argument("--maximum-budget-percent", type=int, default=70)
    parser.add_argument("--top-queries", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    pools = [parse_process_pool(value) for value in args.process_pool]
    runtime: dict[str, Any] | None = None
    if args.database_url:
        runtime = collect_postgresql_snapshot(
            args.database_url,
            top_queries=args.top_queries,
        )

    max_connections = (
        args.database_max_connections
        if args.database_max_connections is not None
        else (runtime or {}).get("database_max_connections")
    )
    reserved = (
        args.reserved_connections
        if args.reserved_connections is not None
        else (runtime or {}).get("reserved_connections", 0)
    )
    if max_connections is None:
        raise SystemExit("database max connections must come from PostgreSQL or --database-max-connections")

    budget = calculate_connection_budget(
        pools,
        database_max_connections=int(max_connections),
        reserved_connections=int(reserved),
        maximum_budget_percent=args.maximum_budget_percent,
    )
    payload = {
        "schema": "nexus.database-capacity-snapshot.v1",
        "status": "pass" if budget["within_budget"] else "fail",
        "budget": budget,
        "runtime": runtime,
        "sanitized": True,
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if budget["within_budget"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
