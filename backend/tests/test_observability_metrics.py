from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/observability_metrics_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.services.observability import (  # noqa: E402
    normalize_metric_path,
    record_db_query,
    record_frontend_api_latency,
    record_openclaw_bridge_metric,
    record_web_vital,
    render_prometheus_metrics,
)


def test_metric_path_normalization_removes_ids() -> None:
    assert normalize_metric_path('/api/tickets/123/messages') == '/api/tickets/{id}/messages'
    assert normalize_metric_path('/api/openclaw/abcdef1234567890/replay') == '/api/openclaw/{id}/replay'


def test_metric_helpers_render_without_high_cardinality_values() -> None:
    record_db_query(2, 'SELECT * FROM tickets WHERE id = %s', slow_threshold_ms=500, request_id='rid-test')
    record_openclaw_bridge_metric('conversation_get', 'success', 12)
    record_frontend_api_latency('/api/tickets/123', 'GET', '200', 18)
    record_web_vital('LCP', 'good', 1.2)

    rendered = render_prometheus_metrics()
    assert 'nexusdesk_db_query_duration_ms' in rendered
    assert 'nexusdesk_openclaw_bridge_elapsed_ms' in rendered
    assert 'nexusdesk_frontend_api_latency_ms' in rendered
    assert 'nexusdesk_web_vitals_value' in rendered
    assert 'SELECT * FROM tickets' not in rendered
    assert '/api/tickets/123' not in rendered
    assert '/api/tickets/{id}' in rendered
