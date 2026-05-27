from __future__ import annotations

import uuid

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.engine import Connection

from .api.admin_outbound_semantics import router as admin_outbound_semantics_router
from .api.admin_perf import router as admin_perf_router
from .api.admin_provider_runtime import router as admin_provider_runtime_router
from .api.admin_provider_credentials import router as admin_provider_credentials_router
from .api.admin_webcall_ai import router as admin_webcall_ai_router
from .api.admin_webcall_ai_demo import router as admin_webcall_ai_demo_router
from .api import admin as admin_api
from .api.admin import router as admin_router
from .api.admin_queue import router as admin_queue_router
from .api.auth import router as auth_router
from .api.channel_control import router as channel_control_router
from .api.customers import router as customers_router
from .api.files import router as files_router
from .api.integration import router as integration_router
from .api.knowledge_items import router as knowledge_items_router
from .api.lookups import router as lookups_router
from .api.lite import router as lite_router
from .api.operator_queue import router as operator_queue_router
from .api.outbound_channels import router as outbound_channels_router
from .api.persona_profiles import router as persona_profiles_router
from .api.speedaf_actions import router as speedaf_actions_router
from .api.speedaf_cancel import router as speedaf_cancel_router
from .api.stats import router as stats_router
from .api.ticket_perf import router as ticket_perf_router
from .api.tickets import router as tickets_router
from .api.webchat_fast import router as webchat_fast_router
from .api.webchat import router as webchat_router
from .api.webchat_events import router as webchat_events_router
from .api.webchat_ws import router as webchat_ws_router
from .api.webchat_voice import router as webchat_voice_router
from .api.webcall_ai import router as webcall_ai_router
from .db import engine, reset_current_request_id, set_current_request_id
from .services.observability import configure_logging, log_event as app_log_event, record_request_metric, render_prometheus_metrics, timed_request
from .services.password_policy import MIN_PASSWORD_LENGTH, PasswordPolicyError, validate_admin_password_policy
from .services.release_metadata import runtime_identity
from .services.spa_fallback_hardening import should_block_spa_fallback
from .services.storage_readiness import check_storage_readiness
from .services.webchat_openclaw_responses_client import close_openclaw_clients
from .settings import get_settings
from .webchat_voice_config import is_webchat_voice_path, load_webchat_voice_runtime_config, webchat_voice_connect_sources

settings = get_settings()
configure_logging(get_settings().log_json)
app = FastAPI(title='NexusDesk Helpdesk', version='20.4.0-round-b')


def _validate_admin_password_or_http(password: str) -> None:
    try:
        validate_admin_password_policy(password)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _custom_openapi() -> dict:
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    schemas = openapi_schema.get('components', {}).get('schemas', {})
    for schema_name in ('UserCreate', 'PasswordResetRequest'):
        password_schema = schemas.get(schema_name, {}).get('properties', {}).get('password')
        if isinstance(password_schema, dict):
            password_schema['minLength'] = MIN_PASSWORD_LENGTH
            password_schema['description'] = 'Admin password must satisfy the production password policy.'
    app.openapi_schema = openapi_schema
    return app.openapi_schema


admin_api._validate_password_length = _validate_admin_password_or_http
app.openapi = _custom_openapi


@app.on_event('shutdown')
async def shutdown_openclaw_clients() -> None:
    await close_openclaw_clients()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=['GET', 'POST', 'PATCH', 'PUT', 'DELETE', 'OPTIONS'],
    allow_headers=['Authorization', 'Content-Type', 'X-API-Key', 'X-Client-Key-Id', 'X-Client-Key', 'Idempotency-Key', 'X-Requested-With', settings.request_id_header],
    expose_headers=[settings.request_id_header],
)

DEFAULT_PERMISSIONS_POLICY = 'camera=(), microphone=(), geolocation=()'
VOICE_PERMISSIONS_POLICY = 'camera=(), microphone=(self), geolocation=()'
DEFAULT_CSP = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"


def _runtime_identity() -> dict[str, str]:
    return runtime_identity(default_app_version=app.version)


def _migration_revision(conn: Connection) -> str | None:
    try:
        row = conn.execute(text('SELECT version_num FROM alembic_version LIMIT 1')).first()
        return str(row[0]) if row and row[0] else None
    except Exception:
        return None


def _frontend_readiness() -> dict[str, object]:
    active_index = settings.frontend_root / 'index.html'
    dist_index_exists = settings.frontend_dist_index.exists()
    active_index_exists = active_index.exists()
    return {
        'ok': active_index_exists and (settings.app_env != 'production' or dist_index_exists),
        'active_root': 'legacy' if settings.frontend_uses_legacy_fallback else 'frontend_dist',
        'frontend_dist_index': 'present' if dist_index_exists else 'missing',
    }


def _voice_runtime_config_for_headers(path: str):
    try:
        return load_webchat_voice_runtime_config()
    except Exception as exc:
        app_log_event(30, 'webchat_voice_runtime_config_unavailable', path=path, error_type=type(exc).__name__)
        return None


def _voice_runtime_headers_enabled(path: str, config=None) -> bool:
    runtime_config = config if config is not None else _voice_runtime_config_for_headers(path)
    if runtime_config is None or not getattr(runtime_config, 'enabled', False):
        return False
    try:
        return bool(is_webchat_voice_path(path, runtime_config))
    except Exception as exc:
        app_log_event(30, 'webchat_voice_runtime_config_unavailable', path=path, error_type=type(exc).__name__)
        return False


def _content_security_policy_for_request(path: str) -> str:
    config = _voice_runtime_config_for_headers(path)
    if not _voice_runtime_headers_enabled(path, config):
        return DEFAULT_CSP
    try:
        connect_src = ["'self'", *webchat_voice_connect_sources(config)]
    except Exception as exc:
        app_log_event(30, 'webchat_voice_runtime_config_unavailable', path=path, error_type=type(exc).__name__)
        return DEFAULT_CSP
    return "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; " + f"connect-src {' '.join(connect_src)}; " + "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"


def _permissions_policy_for_request(path: str) -> str:
    config = _voice_runtime_config_for_headers(path)
    return VOICE_PERMISSIONS_POLICY if _voice_runtime_headers_enabled(path, config) else DEFAULT_PERMISSIONS_POLICY


@app.middleware('http')
async def request_context_middleware(request: Request, call_next):
    request_id = request.headers.get(settings.request_id_header) or uuid.uuid4().hex
    request.state.request_id = request_id
    request_id_token = set_current_request_id(request_id)
    stop_timer = timed_request()
    try:
        response = await call_next(request)
    except Exception as exc:
        duration_ms = stop_timer()
        record_request_metric(request.url.path, request.method, 500, duration_ms)
        app_log_event(40, 'request_failed', request_id=request_id, method=request.method, path=request.url.path, status_code=500, duration_ms=round(duration_ms, 3), error=str(exc))
        raise
    finally:
        reset_current_request_id(request_id_token)
    duration_ms = stop_timer()
    record_request_metric(request.url.path, request.method, response.status_code, duration_ms)
    app_log_event(20, 'request_complete', request_id=request_id, method=request.method, path=request.url.path, status_code=response.status_code, duration_ms=round(duration_ms, 3))
    response.headers.setdefault(settings.request_id_header, request_id)
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'no-referrer')
    response.headers['Permissions-Policy'] = _permissions_policy_for_request(request.url.path)
    response.headers['Content-Security-Policy'] = _content_security_policy_for_request(request.url.path)
    if request.url.path.startswith('/api/'):
        response.headers.setdefault('Cache-Control', 'no-store')
    return response


@app.get('/healthz')
def healthz():
    return {'status': 'ok', 'env': settings.app_env, **_runtime_identity()}


@app.get('/metrics')
def metrics(x_metrics_token: str | None = Header(default=None, alias='X-Metrics-Token')):
    if not settings.metrics_enabled:
        return JSONResponse(status_code=404, content={'detail': 'metrics disabled'})
    if not settings.metrics_token:
        return JSONResponse(status_code=503, content={'detail': 'metrics misconfigured'})
    if x_metrics_token != settings.metrics_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='metrics token required')
    return PlainTextResponse(render_prometheus_metrics(), media_type='text/plain; version=0.0.4')


@app.get('/readyz')
def readyz():
    storage_readiness = check_storage_readiness()
    frontend_readiness = _frontend_readiness()
    try:
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
            migration_revision = _migration_revision(conn)
        ready = storage_readiness.ok and bool(frontend_readiness['ok'])
        payload = {
            'status': 'ready' if ready else 'not_ready',
            'database': 'ok',
            'migration_revision': migration_revision,
            'storage': storage_readiness.as_dict(),
            'frontend': frontend_readiness,
            **_runtime_identity(),
        }
        if not storage_readiness.ok:
            app_log_event(40, 'readiness_storage_check_failed', storage=storage_readiness.as_dict())
            return JSONResponse(status_code=503, content=payload)
        if not frontend_readiness['ok']:
            app_log_event(40, 'readiness_frontend_check_failed', frontend=frontend_readiness)
            return JSONResponse(status_code=503, content=payload)
        if storage_readiness.warnings:
            app_log_event(30, 'readiness_storage_warning', storage=storage_readiness.as_dict())
        return payload
    except Exception as exc:
        app_log_event(40, 'readiness_check_failed', error=str(exc), storage=storage_readiness.as_dict())
        return JSONResponse(status_code=503, content={'status': 'not_ready', 'database': 'error'})


app.include_router(admin_outbound_semantics_router)
app.include_router(admin_perf_router)
app.include_router(admin_provider_runtime_router)
app.include_router(admin_provider_credentials_router)
app.include_router(admin_webcall_ai_router)
app.include_router(admin_webcall_ai_demo_router)
app.include_router(ticket_perf_router)
app.include_router(admin_router)
app.include_router(admin_queue_router)
app.include_router(operator_queue_router)
app.include_router(outbound_channels_router)
app.include_router(auth_router)
app.include_router(channel_control_router)
app.include_router(files_router)
app.include_router(integration_router)
app.include_router(knowledge_items_router)
app.include_router(lookups_router)
app.include_router(lite_router)
app.include_router(customers_router)
app.include_router(persona_profiles_router)
app.include_router(stats_router)
app.include_router(tickets_router)
app.include_router(speedaf_actions_router)
app.include_router(speedaf_cancel_router)
app.include_router(webchat_fast_router)
app.include_router(webchat_events_router)
app.include_router(webchat_ws_router)
app.include_router(webcall_ai_router)
app.include_router(webchat_voice_router)
app.include_router(webchat_router)


@app.get('/webchat/voice/{voice_session_id}', response_class=HTMLResponse)
def serve_webchat_voice_placeholder(voice_session_id: str):
    config = load_webchat_voice_runtime_config()
    route_path = f'/webchat/voice/{voice_session_id}'
    if not config.enabled or not is_webchat_voice_path(route_path, config):
        return JSONResponse(status_code=404, content={'detail': 'WebChat voice is disabled'})
    safe_session_id = ''.join(ch for ch in voice_session_id if ch.isalnum() or ch in {'_', '-'})[:80]
    if not safe_session_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='WebChat voice session not found')
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>NexusDesk WebCall</title></head><body><main>"
        "<h1>NexusDesk WebCall</h1>"
        "<p>Opening the secure WebCall room...</p>"
        f"<p><a href='/webcall/{safe_session_id}'>Continue to WebCall</a></p>"
        f"<script src='/static/webchat/voice-redirect.js' data-voice-session-id='{safe_session_id}' defer></script>"
        "</main></body></html>"
    )


webchat_static_dir = settings.backend_root / 'app' / 'static' / 'webchat'
if webchat_static_dir.exists():
    app.mount('/webchat', StaticFiles(directory=str(webchat_static_dir), html=True), name='webchat_static')
    app.mount('/static/webchat', StaticFiles(directory=str(webchat_static_dir), html=False), name='webchat_embeddable_static')

frontend_dir = settings.frontend_root
assets_dir = frontend_dir / "assets"
if frontend_dir.exists():
    if assets_dir.exists():
        app.mount('/assets', StaticFiles(directory=str(assets_dir)), name='assets')

    @app.get('/', include_in_schema=False)
    def serve_spa_root():
        index_file = frontend_dir / 'index.html'
        if index_file.exists():
            return FileResponse(index_file)
        return JSONResponse(status_code=404, content={'detail': 'frontend build not found'})

    @app.get('/{full_path:path}', include_in_schema=False)
    def serve_spa_fallback(full_path: str):
        if full_path.startswith(('api/', 'docs', 'openapi.json', 'healthz', 'readyz', 'metrics', 'webchat/', 'static/')) or should_block_spa_fallback(full_path):
            return JSONResponse(status_code=404, content={'detail': 'not found'})
        index_file = frontend_dir / 'index.html'
        if index_file.exists():
            return FileResponse(index_file)
        return JSONResponse(status_code=404, content={'detail': 'frontend build not found'})
