from __future__ import annotations

import uuid

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

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
from .api.persona_profiles import router as persona_profiles_router
from .api.stats import router as stats_router
from .api.tickets import router as tickets_router
from .api.webchat import router as webchat_router
from .db import engine
from .services.observability import configure_logging, log_event as app_log_event, record_request_metric, render_prometheus_metrics, timed_request
from .settings import get_settings

settings = get_settings()
configure_logging(get_settings().log_json)
app = FastAPI(title='NexusDesk Helpdesk', version='20.4.0-round-b')

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=['GET', 'POST', 'PATCH', 'PUT', 'DELETE', 'OPTIONS'],
    allow_headers=['Authorization', 'Content-Type', 'X-API-Key', 'X-Client-Key-Id', 'X-Client-Key', 'Idempotency-Key', 'X-Requested-With', settings.request_id_header],
    expose_headers=[settings.request_id_header],
)


@app.middleware('http')
async def request_context_middleware(request: Request, call_next):
    request_id = request.headers.get(settings.request_id_header) or uuid.uuid4().hex
    request.state.request_id = request_id
    stop_timer = timed_request()
    try:
        response = await call_next(request)
    except Exception as exc:
        duration_ms = stop_timer()
        record_request_metric(request.url.path, request.method, 500, duration_ms)
        app_log_event(40, 'request_failed', request_id=request_id, method=request.method, path=request.url.path, status_code=500, duration_ms=round(duration_ms, 3), error=str(exc))
        raise
    duration_ms = stop_timer()
    record_request_metric(request.url.path, request.method, response.status_code, duration_ms)
    app_log_event(20, 'request_complete', request_id=request_id, method=request.method, path=request.url.path, status_code=response.status_code, duration_ms=round(duration_ms, 3))
    response.headers.setdefault(settings.request_id_header, request_id)
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'no-referrer')
    response.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
    response.headers.setdefault('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
    if request.url.path.startswith('/api/'):
        response.headers.setdefault('Cache-Control', 'no-store')
    return response


@app.get('/healthz')
def healthz():
    return {'status': 'ok', 'env': settings.app_env}


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
    try:
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        return {'status': 'ready', 'database': 'ok'}
    except Exception as exc:
        app_log_event(40, 'readiness_check_failed', error=str(exc))
        return JSONResponse(status_code=503, content={'status': 'not_ready', 'database': 'error'})


app.include_router(admin_router)
app.include_router(admin_queue_router)
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
app.include_router(webchat_router)

webchat_static_dir = settings.backend_root / 'app' / 'static' / 'webchat'
if webchat_static_dir.exists():
    app.mount('/webchat', StaticFiles(directory=str(webchat_static_dir), html=True), name='webchat_static')
    app.mount('/static/webchat', StaticFiles(directory=str(webchat_static_dir), html=False), name='webchat_embeddable_static')

frontend_dir = settings.frontend_root
assets_dir = frontend_dir / "assets"
if frontend_dir.exists():
    if assets_dir.exists():
        app.mount('/assets', StaticFiles(directory=str(assets_dir)), name='frontend_assets')

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        if full_path.startswith("webchat/") or full_path.startswith("static/webchat/"):
            return JSONResponse(status_code=404, content={"detail": "Webchat asset not found"})
        file_path = frontend_dir / full_path
        if file_path.is_file():
            return FileResponse(str(file_path))
        index_path = frontend_dir / "index.html"
        if index_path.is_file():
            return FileResponse(str(index_path))
        return JSONResponse(status_code=404, content={"detail": "Frontend not built"})
