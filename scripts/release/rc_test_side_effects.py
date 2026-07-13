#!/usr/bin/env python3
"""Extend the merged RC side-effect checker with durable ToolCallLog evidence."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_BASE_PATH = Path(__file__).with_name("rc_test_side_effects_base.py")
_SPEC = importlib.util.spec_from_file_location("nexus_rc_side_effects_base", _BASE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise SystemExit("rc_side_effect_base_unavailable")
_base = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_base)

_base.MISSING_TABLE_EXIT_CODES["tool_call_logs"] = 41
_base.REQUIRED_EVIDENCE_TABLES = tuple(_base.MISSING_TABLE_EXIT_CODES)
_original_collect = _base._collect_semantic_execution_counts


def _collect_semantic_execution_counts(db: Any) -> dict[str, int]:
    counts = _original_collect(db)
    tool_call_log_execution_count = _base._count(
        db,
        """
        SELECT COUNT(*)
        FROM tool_call_logs
        WHERE lower(BTRIM(COALESCE(status, ''))) IN ('success', 'executed')
        """,
    )
    counts["tool_call_log_execution_count"] = tool_call_log_execution_count
    counts["external_tool_execution_count"] += tool_call_log_execution_count
    return counts


_base._collect_semantic_execution_counts = _collect_semantic_execution_counts


if __name__ == "__main__":
    raise SystemExit(_base.main())
