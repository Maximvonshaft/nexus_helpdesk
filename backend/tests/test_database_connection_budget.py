from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "database_capacity",
    ROOT / "scripts" / "qualification" / "database_capacity.py",
)
assert SPEC is not None and SPEC.loader is not None
database_capacity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(database_capacity)


def test_parse_process_pool_and_maximum_connections():
    pool = database_capacity.parse_process_pool("web:2:5:5")
    assert pool.role == "web"
    assert pool.maximum_connections == 20


def test_current_default_topology_fits_seventy_percent_budget():
    pools = [
        database_capacity.parse_process_pool("web:2:5:5"),
        database_capacity.parse_process_pool("outbound:1:2:2"),
        database_capacity.parse_process_pool("background:1:3:2"),
        database_capacity.parse_process_pool("webchat-ai:1:2:1"),
        database_capacity.parse_process_pool("handoff:1:2:1"),
    ]
    result = database_capacity.calculate_connection_budget(
        pools,
        database_max_connections=100,
        reserved_connections=10,
    )
    assert result["configured_application_max_connections"] == 35
    assert result["within_budget"] is True
    assert result["remaining_connections_at_configured_max"] == 65


def test_oversubscribed_topology_fails_budget():
    pools = [database_capacity.parse_process_pool("web:8:10:20")]
    result = database_capacity.calculate_connection_budget(
        pools,
        database_max_connections=200,
        reserved_connections=20,
    )
    assert result["configured_application_max_connections"] == 240
    assert result["within_budget"] is False


@pytest.mark.parametrize(
    "value",
    [
        "",
        "web:2:5",
        "web:zero:5:5",
        "web:0:5:5",
        "web:2:0:5",
        "web:2:5:-1",
    ],
)
def test_invalid_process_pool_is_rejected(value):
    with pytest.raises(ValueError):
        database_capacity.parse_process_pool(value)
