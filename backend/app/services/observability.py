from __future__ import annotations

import json
import logging
import re
from time import perf_counter

LOGGER = logging.getLogger("nexusdesk")

try:
    from prometheus_client import CollectorRegistry, CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest, multiprocess
except Exception:  # pragma: no cover
    CollectorRegistry = None
    CONTENT_TYPE_LATEST = 'text/plain; version=0.0.4'
    Counter = None
    Gauge = None
    Histogram = None
    generate_latest = None
    multiprocess = None


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"level": record.levelname, "logger": record.name, "message": record.getMessage()}
        extra = getattr(record, "event_payload", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False)


_PROM_REGISTRY = CollectorRegistry() if CollectorRegistry else None
if _PROM_REGISTRY and multiprocess:
    try:
        multiprocess.MultiProcessCollector(_PROM_REGISTRY)
    except ValueError:
        pass

_HTTP_COUNTER = Counter('nexusdesk_http_requests_total', 'Total HTTP requests processed', ['method', 'path', 'status_code'], registry=_PROM_REGISTRY) if Counter else None
_HTTP_DURATION = Histogram('nexusdesk_http_request_duration_ms', 'HTTP request duration in milliseconds', ['method', 'path'], registry=_PROM_REGISTRY, buckets=(5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000)) if Histogram else None
_WORKER_RUNS = Counter('nexusdesk_worker_runs_total', 'Number of worker polling cycles', ['worker_id'], registry=_PROM_REGISTRY) if Counter else None
_JOB_COUNTER = Counter('nexusdesk_worker_processed_total', 'Processed queued jobs/messages', ['worker_id', 'kind', 'result'], registry=_PROM_REGISTRY) if Counter else None
_QUEUE_DEPTH = Gauge('nexusdesk_queue_depth', 'Observed queue depth or per-cycle queue count', ['name', 'kind'], registry=_PROM_REGISTRY) if Gauge else None
_QUEUE_SNAPSHOTS_COMPAT = Counter('nexusdesk_queue_snapshots_total', 'Backward-compatible queue snapshot observations', ['name', 'kind'], registry=_PROM_REGISTRY) if Counter else None
_WEBCHAT_AI_TURNS = Counter('nexusdesk_webchat_ai_turn_total', 'WebChat AI turn status transitions', ['status'], registry=_PROM_REGISTRY) if Counter else None
_WEBCHAT_AI_TURN_DURATION = Histogram('nexusdesk_webchat_ai_turn_duration_ms', 'WebChat AI turn duration in milliseconds', ['status'], registry=_PROM_REGISTRY, buckets=(50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000, 120000)) if Histogram else None
_WEBCHAT_AI_STALE_SUPPRESSED = Counter('nexusdesk_webchat_ai_stale_suppressed_total', 'Stale WebChat AI replies suppressed before public delivery', ['reason'], registry=_PROM_REGISTRY) if Counter else None
_WEBCHAT_AI_TIMEOUTS = Counter('nexusdesk_webchat_ai_timeout_total', 'WebChat AI turn watchdog timeouts', ['reason'], registry=_PROM_REGISTRY) if Counter else None
_OPENCLAW_BRIDGE_DURATION = Histogram('nexusdesk_openclaw_bridge_elapsed_ms', 'OpenClaw bridge call duration in milliseconds', ['operation', 'status'], registry=_PROM_REGISTRY, buckets=(50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000)) if Histogram else None
_TOOL_CALLS = Counter('nexusdesk_tool_call_total', 'Tool governance audit call count', ['tool_name', 'tool_type', 'status'], registry=_PROM_REGISTRY) if Counter else None
_TOOL_CALL_DURATION = Histogram('nexusdesk_tool_call_elapsed_ms', 'Tool governance audit call duration in milliseconds', ['tool_name', 'tool_type', 'status'], registry=_PROM_REGISTRY, buckets=(10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000)) if Histogram else None
_BACKGROUND_JOB_WAIT = Histogram('nexusdesk_background_job_wait_ms', 'Background job wait time before processing in milliseconds', ['job_type'], registry=_PROM_REGISTRY, buckets=(10, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000, 300000)) if Histogram else None
_DB_QUERY_DURATION = Histogram('nexusdesk_db_query_duration_ms', 'Database query duration in milliseconds', ['category'], registry=_PROM_REGISTRY, buckets=(1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000)) if Histogram else None
_DB_SLOW_QUERY_TOTAL = Counter('nexusdesk_db_slow_query_total', 'Database queries exceeding DB_SLOW_QUERY_MS', ['category'], registry=_PROM_REGISTRY) if Counter else None
_BACKGROUND_JOB_DURATION = Histogram('nexusdesk_worker_job_duration_ms', 'Background job processing duration in milliseconds', ['job_type', 'result'], registry=_PROM_REGISTRY, buckets=(10, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000, 300000)) if Histogram else None
_BACKGROUND_JOB_RETRIES = Counter('nexusdesk_worker_job_retries_total', 'Background job retry count', ['job_type', 'result'], registry=_PROM_REGISTRY) if Counter else None
_BACKGROUND_JOB_OLDEST_PENDING = Gauge('nexusdesk_worker_oldest_pending_job_age_ms', 'Oldest pending job age in milliseconds by job type', ['job_type'], registry=_PROM_REGISTRY) if Gauge else None
_OUTBOUND_QUEUED_TO_SENT = Histogram('nexusdesk_outbound_queued_to_sent_ms', 'Outbound queued_at to sent_at latency in milliseconds', ['channel', 'provider', 'status'], registry=_PROM_REGISTRY, buckets=(10, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000, 300000)) if Histogram else None
_OUTBOUND_PROVIDER_DISPATCH = Histogram('nexusdesk_outbound_provider_dispatch_ms', 'Outbound provider dispatch duration in milliseconds', ['provider', 'status'], registry=_PROM_REGISTRY, buckets=(10, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000)) if Histogram else None
_OUTBOUND_PROVIDER_RESULT = Counter('nexusdesk_outbound_provider_result_total', 'Outbound provider dispatch result count', ['provider', 'status'], registry=_PROM_REGISTRY) if Counter else None
_FRONTEND_API_LATENCY = Histogram('nexusdesk_frontend_api_latency_ms', 'Frontend-observed API latency in milliseconds', ['method', 'path', 'status'], registry=_PROM_REGISTRY, buckets=(25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 15000)) if Histogram else None
_WEB_VITALS = Histogram('nexusdesk_web_vitals_value', 'Frontend Web Vitals values reported without PII', ['name', 'rating'], registry=_PROM_REGISTRY, buckets=(0.001, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)) if Histogram else None

_ID_SEGMENT_RE = re.compile(r"/\d+(?=/|$)")
_UUID_SEGMENT_RE = re.compile(r"/[0-9a-fA-F]{8,}(?=/|$)")
_SQL_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def normalize_metric_path(path: str) -> str:
    normalized = _UUID_SEGMENT_RE.sub('/{id}', path or '/')
    normalized = _ID_SEGMENT_RE.sub('/{id}', normalized)
    return normalized or '/'


def _label(value: str | None, default: str = 'unknown') -> str:
    safe = (value or default).strip() or default
    return safe[:80]


def sql_statement_category(statement: str | None) -> str:
    raw = _SQL_COMMENT_RE.sub(' ', statement or '').lstrip().lower()
    if not raw:
        return 'unknown'
    first = raw.split(None, 1)[0]
    if first in {'select', 'insert', 'update', 'delete', 'commit', 'rollback', 'begin'}:
        return first
    if first in {'alter', 'create', 'drop'}:
        return 'ddl'
    return 'other'


def configure_logging(log_json: bool = True) -> None:
    if getattr(configure_logging, "_configured", False):
        return
    handler = logging.StreamHandler()
    if log_json:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    configure_logging._configured = True


def log_event(level: int, message: str, **payload) -> None:
    LOGGER.log(level, message, extra={"event_payload": payload})


def record_request_metric(path: str, method: str, status_code: int, duration_ms: float) -> None:
    safe_path = normalize_metric_path(path)
    if _HTTP_COUNTER:
        _HTTP_COUNTER.labels(method=method, path=safe_path, status_code=str(status_code)).inc()
    if _HTTP_DURATION:
        _HTTP_DURATION.labels(method=method, path=safe_path).observe(duration_ms)


def record_worker_poll(worker_id: str) -> None:
    if _WORKER_RUNS:
        _WORKER_RUNS.labels(worker_id=worker_id).inc()


def record_worker_result(worker_id: str, kind: str, result: str, count: int = 1) -> None:
    if _JOB_COUNTER:
        _JOB_COUNTER.labels(worker_id=worker_id, kind=kind, result=result).inc(count)


def render_prometheus_metrics() -> str:
    if generate_latest and _PROM_REGISTRY:
        return generate_latest(_PROM_REGISTRY).decode('utf-8')
    return "# metrics disabled\n"


def timed_request():
    start = perf_counter()
    return lambda: (perf_counter() - start) * 1000.0


def record_queue_snapshot(name: str, kind: str, value: int = 1) -> None:
    if _QUEUE_DEPTH:
        _QUEUE_DEPTH.labels(name=name, kind=kind).set(value)
    if _QUEUE_SNAPSHOTS_COMPAT:
        _QUEUE_SNAPSHOTS_COMPAT.labels(name=name, kind=kind).inc(max(value, 0))


def record_webchat_ai_turn_metric(status: str, duration_ms: int | float | None = None) -> None:
    safe_status = _label(status, 'unknown')
    if _WEBCHAT_AI_TURNS:
        _WEBCHAT_AI_TURNS.labels(status=safe_status).inc()
    if duration_ms is not None and _WEBCHAT_AI_TURN_DURATION:
        _WEBCHAT_AI_TURN_DURATION.labels(status=safe_status).observe(max(float(duration_ms), 0.0))


def record_webchat_ai_stale_suppressed(reason: str | None = None) -> None:
    if _WEBCHAT_AI_STALE_SUPPRESSED:
        _WEBCHAT_AI_STALE_SUPPRESSED.labels(reason=_label(reason, 'unknown')).inc()


def record_webchat_ai_timeout(reason: str | None = None) -> None:
    if _WEBCHAT_AI_TIMEOUTS:
        _WEBCHAT_AI_TIMEOUTS.labels(reason=_label(reason, 'unknown')).inc()


def record_openclaw_bridge_metric(operation: str, status: str, elapsed_ms: int | float | None = None) -> None:
    if elapsed_ms is not None and _OPENCLAW_BRIDGE_DURATION:
        _OPENCLAW_BRIDGE_DURATION.labels(operation=_label(operation), status=_label(status)).observe(max(float(elapsed_ms), 0.0))


def record_tool_call_metric(tool_name: str, tool_type: str, status: str, elapsed_ms: int | float | None = None) -> None:
    safe_tool_name = _label(tool_name)
    safe_tool_type = _label(tool_type)
    safe_status = _label(status)
    if _TOOL_CALLS:
        _TOOL_CALLS.labels(tool_name=safe_tool_name, tool_type=safe_tool_type, status=safe_status).inc()
    if elapsed_ms is not None and _TOOL_CALL_DURATION:
        _TOOL_CALL_DURATION.labels(tool_name=safe_tool_name, tool_type=safe_tool_type, status=safe_status).observe(max(float(elapsed_ms), 0.0))


def record_background_job_wait(job_type: str, wait_ms: int | float | None) -> None:
    if wait_ms is not None and _BACKGROUND_JOB_WAIT:
        _BACKGROUND_JOB_WAIT.labels(job_type=_label(job_type)).observe(max(float(wait_ms), 0.0))


def record_db_query(duration_ms: int | float, statement: str | None = None, *, slow_threshold_ms: int | float | None = None, request_id: str | None = None) -> None:
    category = sql_statement_category(statement)
    safe_duration = max(float(duration_ms), 0.0)
    if _DB_QUERY_DURATION:
        _DB_QUERY_DURATION.labels(category=category).observe(safe_duration)
    if slow_threshold_ms is not None and safe_duration >= float(slow_threshold_ms):
        if _DB_SLOW_QUERY_TOTAL:
            _DB_SLOW_QUERY_TOTAL.labels(category=category).inc()
        LOGGER.warning(
            'db_slow_query',
            extra={'event_payload': {
                'duration_ms': round(safe_duration, 2),
                'category': category,
                'request_id': _label(request_id, 'none'),
            }},
        )


def record_worker_job_metric(job_type: str, result: str, duration_ms: int | float | None = None, wait_ms: int | float | None = None, retry_count: int | None = None) -> None:
    safe_job_type = _label(job_type)
    safe_result = _label(result)
    if wait_ms is not None:
        record_background_job_wait(safe_job_type, wait_ms)
    if duration_ms is not None and _BACKGROUND_JOB_DURATION:
        _BACKGROUND_JOB_DURATION.labels(job_type=safe_job_type, result=safe_result).observe(max(float(duration_ms), 0.0))
    if retry_count and retry_count > 0 and _BACKGROUND_JOB_RETRIES:
        _BACKGROUND_JOB_RETRIES.labels(job_type=safe_job_type, result=safe_result).inc(int(retry_count))


def record_oldest_pending_job_age(job_type: str, age_ms: int | float | None) -> None:
    if age_ms is not None and _BACKGROUND_JOB_OLDEST_PENDING:
        _BACKGROUND_JOB_OLDEST_PENDING.labels(job_type=_label(job_type)).set(max(float(age_ms), 0.0))


def record_outbound_latency(channel: str | None, provider: str | None, status: str, queued_to_sent_ms: int | float | None = None, provider_dispatch_ms: int | float | None = None) -> None:
    safe_channel = _label(channel)
    safe_provider = _label(provider)
    safe_status = _label(status)
    if queued_to_sent_ms is not None and _OUTBOUND_QUEUED_TO_SENT:
        _OUTBOUND_QUEUED_TO_SENT.labels(channel=safe_channel, provider=safe_provider, status=safe_status).observe(max(float(queued_to_sent_ms), 0.0))
    if provider_dispatch_ms is not None and _OUTBOUND_PROVIDER_DISPATCH:
        _OUTBOUND_PROVIDER_DISPATCH.labels(provider=safe_provider, status=safe_status).observe(max(float(provider_dispatch_ms), 0.0))
    if _OUTBOUND_PROVIDER_RESULT:
        _OUTBOUND_PROVIDER_RESULT.labels(provider=safe_provider, status=safe_status).inc()


def record_frontend_api_latency(path: str, method: str, status: str, duration_ms: int | float | None) -> None:
    if duration_ms is not None and _FRONTEND_API_LATENCY:
        _FRONTEND_API_LATENCY.labels(method=_label(method), path=normalize_metric_path(path), status=_label(status)).observe(max(float(duration_ms), 0.0))


def record_web_vital(name: str, rating: str, value: int | float | None) -> None:
    if value is not None and _WEB_VITALS:
        _WEB_VITALS.labels(name=_label(name), rating=_label(rating)).observe(max(float(value), 0.0))


def log_signoff_state(state: str, **fields) -> None:
    log_event(20, "production_signoff_state", state=state, **fields)
