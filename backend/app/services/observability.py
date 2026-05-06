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

_ID_SEGMENT_RE = re.compile(r"/\d+(?=/|$)")
_UUID_SEGMENT_RE = re.compile(r"/[0-9a-fA-F]{8,}(?=/|$)")


def normalize_metric_path(path: str) -> str:
    normalized = _UUID_SEGMENT_RE.sub('/{id}', path or '/')
    normalized = _ID_SEGMENT_RE.sub('/{id}', normalized)
    return normalized or '/'


def _label(value: str | None, default: str = 'unknown') -> str:
    safe = (value or default).strip() or default
    return safe[:80]


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


def log_signoff_state(state: str, **fields) -> None:
    log_event(20, "production_signoff_state", state=state, **fields)
