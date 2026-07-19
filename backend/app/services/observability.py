from __future__ import annotations

import atexit
import json
import logging
import os
import re
from time import perf_counter

from .log_sanitizer import build_safe_log_payload, sanitize_log_event

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
        try:
            payload = build_safe_log_payload(
                level=record.levelname,
                logger=record.name,
                message=record.getMessage(),
                event_payload=getattr(record, "event_payload", None),
            )
            return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            return '{"level":"ERROR","logger":"logging","message":"log_formatter_failure","redacted":true}'


class _SafeTextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        try:
            payload = build_safe_log_payload(
                level=record.levelname,
                logger=record.name,
                message=record.getMessage(),
                event_payload=getattr(record, "event_payload", None),
            )
            event = {key: value for key, value in payload.items() if key not in {"level", "logger", "message"}}
            suffix = f" {json.dumps(event, ensure_ascii=False, sort_keys=True, default=str)}" if event else ""
            return f"{payload['level']} {payload['logger']} {payload['message']}{suffix}"
        except Exception:
            return "ERROR logging log_formatter_failure"


_PROMETHEUS_MULTIPROC_DIR = (os.getenv("PROMETHEUS_MULTIPROC_DIR") or "").strip()
_PROMETHEUS_MULTIPROC_ENABLED = bool(
    _PROMETHEUS_MULTIPROC_DIR
    and CollectorRegistry is not None
    and multiprocess is not None
)
# In multiprocess mode, metric wrappers must not be registered in the scrape
# registry. They write per-process files and a fresh MultiProcessCollector reads
# those files for every scrape. In single-process mode, keep the existing private
# registry so tests and local deployments do not pollute the default registry.
_PROM_REGISTRY = None if _PROMETHEUS_MULTIPROC_ENABLED else (CollectorRegistry() if CollectorRegistry else None)


def _registry():
    return _PROM_REGISTRY


def _counter(name: str, description: str, labels: list[str]):
    return Counter(name, description, labels, registry=_registry()) if Counter else None


def _histogram(name: str, description: str, labels: list[str], buckets: tuple[float, ...]):
    return Histogram(name, description, labels, registry=_registry(), buckets=buckets) if Histogram else None


def _gauge(name: str, description: str, labels: list[str], *, multiprocess_mode: str = "all"):
    if not Gauge:
        return None
    kwargs = {"registry": _registry()}
    if _PROMETHEUS_MULTIPROC_ENABLED:
        kwargs["multiprocess_mode"] = multiprocess_mode
    return Gauge(name, description, labels, **kwargs)


_HTTP_COUNTER = _counter('nexusdesk_http_requests_total', 'Total HTTP requests processed', ['method', 'path', 'status_code'])
_HTTP_DURATION = _histogram('nexusdesk_http_request_duration_ms', 'HTTP request duration in milliseconds', ['method', 'path'], (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000))
_WORKER_RUNS = _counter('nexusdesk_worker_runs_total', 'Number of worker polling cycles', ['worker_id'])
_JOB_COUNTER = _counter('nexusdesk_worker_processed_total', 'Processed queued jobs/messages', ['worker_id', 'kind', 'result'])
_QUEUE_DEPTH = _gauge('nexusdesk_queue_depth', 'Observed queue depth or per-cycle queue count', ['name', 'kind'], multiprocess_mode='livesum')
_QUEUE_SNAPSHOTS_COMPAT = _counter('nexusdesk_queue_snapshots_total', 'Backward-compatible queue snapshot observations', ['name', 'kind'])
_WEBCHAT_AI_TURNS = _counter('nexusdesk_webchat_ai_turn_total', 'WebChat AI turn status transitions', ['status'])
_WEBCHAT_AI_TURN_DURATION = _histogram('nexusdesk_webchat_ai_turn_duration_ms', 'WebChat AI turn duration in milliseconds', ['status'], (50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000, 120000))
_WEBCHAT_AI_STALE_SUPPRESSED = _counter('nexusdesk_webchat_ai_stale_suppressed_total', 'Stale WebChat AI replies suppressed before public delivery', ['reason'])
_WEBCHAT_AI_TIMEOUTS = _counter('nexusdesk_webchat_ai_timeout_total', 'WebChat AI turn watchdog timeouts', ['reason'])
_TOOL_CALLS = _counter('nexusdesk_tool_call_total', 'Tool governance audit call count', ['tool_name', 'tool_type', 'status'])
_TOOL_CALL_DURATION = _histogram('nexusdesk_tool_call_elapsed_ms', 'Tool governance audit call duration in milliseconds', ['tool_name', 'tool_type', 'status'], (10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000))
_BACKGROUND_JOB_WAIT = _histogram('nexusdesk_background_job_wait_ms', 'Background job wait time before processing in milliseconds', ['job_type'], (10, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000, 300000))
_DB_QUERY_DURATION = _histogram('nexusdesk_db_query_duration_ms', 'Database query duration in milliseconds', ['category'], (1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000))
_DB_SLOW_QUERY_TOTAL = _counter('nexusdesk_db_slow_query_total', 'Database queries exceeding DB_SLOW_QUERY_MS', ['category'])
_BACKGROUND_JOB_DURATION = _histogram('nexusdesk_worker_job_duration_ms', 'Background job processing duration in milliseconds', ['job_type', 'result'], (10, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000, 300000))
_BACKGROUND_JOB_RETRIES = _counter('nexusdesk_worker_job_retries_total', 'Background job retry count', ['job_type', 'result'])
_BACKGROUND_JOB_OLDEST_PENDING = _gauge('nexusdesk_worker_oldest_pending_job_age_ms', 'Oldest pending job age in milliseconds by job type', ['job_type'], multiprocess_mode='livemax')
_OUTBOUND_QUEUED_TO_SENT = _histogram('nexusdesk_outbound_queued_to_sent_ms', 'Outbound queued_at to sent_at latency in milliseconds', ['channel', 'provider', 'status'], (10, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000, 300000))
_OUTBOUND_PROVIDER_DISPATCH = _histogram('nexusdesk_outbound_provider_dispatch_ms', 'Outbound provider dispatch duration in milliseconds', ['provider', 'status'], (10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000))
_OUTBOUND_PROVIDER_RESULT = _counter('nexusdesk_outbound_provider_result_total', 'Outbound provider dispatch result count', ['provider', 'status'])
_FRONTEND_API_LATENCY = _histogram('nexusdesk_frontend_api_latency_ms', 'Frontend-observed API latency in milliseconds', ['method', 'path', 'status'], (25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 15000))
_WEB_VITALS = _histogram('nexusdesk_web_vitals_value', 'Frontend Web Vitals values reported without PII', ['name', 'rating'], (0.001, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
_VOICE_SESSION_EVENTS = _counter('nexusdesk_voice_session_events_total', 'Voice session lifecycle events without credentials', ['provider', 'status', 'event_type'])
_VOICE_PROVIDER_ERRORS = _counter('nexusdesk_voice_provider_errors_total', 'Voice provider operation errors', ['provider', 'operation'])
_VOICE_CALL_DURATION = _histogram('nexusdesk_voice_call_duration_seconds', 'Completed voice call duration in seconds', ['provider', 'status'], (1, 5, 10, 30, 60, 120, 300, 600, 900, 1800, 3600))
_VOICE_RINGING_DURATION = _histogram('nexusdesk_voice_ringing_duration_seconds', 'Voice ringing duration before accept or terminal state in seconds', ['provider', 'status'], (1, 2, 5, 10, 20, 30, 60, 120, 300, 600, 900))
_WEBCHAT_WS_CONNECTED = _counter('nexusdesk_webchat_websocket_connected_total', 'WebChat WebSocket accepted connections', ['client_type'])
_WEBCHAT_WS_DISCONNECTED = _counter('nexusdesk_webchat_websocket_disconnected_total', 'WebChat WebSocket closed connections', ['client_type'])
_WEBCHAT_WS_AUTH_FAILED = _counter('nexusdesk_webchat_websocket_auth_failed_total', 'WebChat WebSocket rejected handshakes and subscriptions', ['client_type', 'reason'])
_WEBCHAT_WS_EVENT_SENT = _counter('nexusdesk_webchat_websocket_event_sent_total', 'WebChat WebSocket events sent to clients', ['client_type', 'event_type'])
_WEBCHAT_WS_EVENT_REPLAY = _counter('nexusdesk_webchat_websocket_event_replay_total', 'WebChat WebSocket replay batches delivered', ['client_type', 'subscription'])
_WEBCHAT_WS_FALLBACK_POLLING = _counter('nexusdesk_webchat_websocket_fallback_polling_total', 'WebChat public widget fallback polling activations', ['client_type', 'reason'])
_WEBCHAT_WS_ACTIVE_CONNECTIONS = _gauge('nexusdesk_webchat_websocket_active_connections', 'Current in-process WebChat WebSocket connections', ['client_type'], multiprocess_mode='livesum')

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
    handler.setFormatter(_JsonFormatter() if log_json else _SafeTextFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    for logger_name in ("httpx", "httpcore"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
    configure_logging._configured = True


def log_event(level: int, message: str, **payload) -> None:
    LOGGER.log(level, message, extra={"event_payload": sanitize_log_event(payload)})


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
    if not generate_latest:
        return "# metrics disabled\n"
    if _PROMETHEUS_MULTIPROC_ENABLED:
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return generate_latest(registry).decode('utf-8')
    if _PROM_REGISTRY:
        return generate_latest(_PROM_REGISTRY).decode('utf-8')
    return "# metrics disabled\n"


def mark_prometheus_process_dead(pid: int | None = None) -> None:
    if not _PROMETHEUS_MULTIPROC_ENABLED:
        return
    try:
        multiprocess.mark_process_dead(int(pid or os.getpid()))
    except Exception:
        # Metrics cleanup is best-effort and must never interrupt process exit.
        pass


if _PROMETHEUS_MULTIPROC_ENABLED:
    atexit.register(mark_prometheus_process_dead)


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


def record_webchat_websocket_connected(client_type: str | None) -> None:
    if _WEBCHAT_WS_CONNECTED:
        _WEBCHAT_WS_CONNECTED.labels(client_type=_label(client_type)).inc()


def record_webchat_websocket_disconnected(client_type: str | None) -> None:
    if _WEBCHAT_WS_DISCONNECTED:
        _WEBCHAT_WS_DISCONNECTED.labels(client_type=_label(client_type)).inc()


def record_webchat_websocket_auth_failed(client_type: str | None, reason: str | None) -> None:
    if _WEBCHAT_WS_AUTH_FAILED:
        _WEBCHAT_WS_AUTH_FAILED.labels(client_type=_label(client_type), reason=_label(reason)).inc()


def record_webchat_websocket_event_sent(client_type: str | None, event_type: str | None) -> None:
    if _WEBCHAT_WS_EVENT_SENT:
        _WEBCHAT_WS_EVENT_SENT.labels(client_type=_label(client_type), event_type=_label(event_type)).inc()


def record_webchat_websocket_event_replay(client_type: str | None, subscription: str | None, count: int = 1) -> None:
    if _WEBCHAT_WS_EVENT_REPLAY:
        _WEBCHAT_WS_EVENT_REPLAY.labels(client_type=_label(client_type), subscription=_label(subscription)).inc(max(int(count or 0), 0))


def record_webchat_websocket_fallback_polling(client_type: str | None, reason: str | None) -> None:
    if _WEBCHAT_WS_FALLBACK_POLLING:
        _WEBCHAT_WS_FALLBACK_POLLING.labels(client_type=_label(client_type), reason=_label(reason)).inc()


def record_webchat_websocket_active_connections(*, agents: int = 0, visitors: int = 0) -> None:
    if _WEBCHAT_WS_ACTIVE_CONNECTIONS:
        _WEBCHAT_WS_ACTIVE_CONNECTIONS.labels(client_type='agent').set(max(int(agents or 0), 0))
        _WEBCHAT_WS_ACTIVE_CONNECTIONS.labels(client_type='visitor').set(max(int(visitors or 0), 0))
        _WEBCHAT_WS_ACTIVE_CONNECTIONS.labels(client_type='all').set(max(int(agents or 0), 0) + max(int(visitors or 0), 0))


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
            extra={'event_payload': sanitize_log_event({
                'duration_ms': round(safe_duration, 2),
                'category': category,
                'request_id': _label(request_id, 'none'),
            })},
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


def record_voice_session_event(provider: str | None, status: str | None, event_type: str | None) -> None:
    if _VOICE_SESSION_EVENTS:
        _VOICE_SESSION_EVENTS.labels(provider=_label(provider), status=_label(status), event_type=_label(event_type)).inc()


def record_voice_provider_error(provider: str | None, operation: str | None) -> None:
    if _VOICE_PROVIDER_ERRORS:
        _VOICE_PROVIDER_ERRORS.labels(provider=_label(provider), operation=_label(operation)).inc()


def record_voice_call_duration(provider: str | None, status: str | None, duration_seconds: int | float | None) -> None:
    if duration_seconds is not None and _VOICE_CALL_DURATION:
        _VOICE_CALL_DURATION.labels(provider=_label(provider), status=_label(status)).observe(max(float(duration_seconds), 0.0))


def record_voice_ringing_duration(provider: str | None, status: str | None, duration_seconds: int | float | None) -> None:
    if duration_seconds is not None and _VOICE_RINGING_DURATION:
        _VOICE_RINGING_DURATION.labels(provider=_label(provider), status=_label(status)).observe(max(float(duration_seconds), 0.0))


def log_signoff_state(state: str, **fields) -> None:
    log_event(20, "production_signoff_state", state=state, **fields)
