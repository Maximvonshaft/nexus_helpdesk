from contextlib import contextmanager
from contextvars import ContextVar
import os
from time import perf_counter
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .settings import get_settings

settings = get_settings()
DATABASE_URL = settings.database_url

_current_request_id: ContextVar[str | None] = ContextVar('nexusdesk_request_id', default=None)


def set_current_request_id(request_id: str | None):
    return _current_request_id.set(request_id)


def reset_current_request_id(token) -> None:
    _current_request_id.reset(token)


def get_current_request_id() -> str | None:
    return _current_request_id.get()


def _db_query_timing_enabled() -> bool:
    return os.getenv('DB_QUERY_TIMING_ENABLED', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}


def _slow_query_ms() -> float:
    try:
        return float(os.getenv('DB_SLOW_QUERY_MS', '500'))
    except ValueError:
        return 500.0


def _bounded_int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise RuntimeError(f'{name}_invalid') from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f'{name}_out_of_range')
    return value


def database_pool_configuration() -> dict[str, int | str]:
    """Return the sole sanitized SQLAlchemy pool contract for this process."""
    pool_size = _bounded_int_env('DB_POOL_SIZE', 5, minimum=1, maximum=50)
    max_overflow = _bounded_int_env('DB_MAX_OVERFLOW', 5, minimum=0, maximum=50)
    pool_timeout_seconds = _bounded_int_env(
        'DB_POOL_TIMEOUT_SECONDS',
        10,
        minimum=1,
        maximum=120,
    )
    pool_recycle_seconds = _bounded_int_env(
        'DB_POOL_RECYCLE_SECONDS',
        1800,
        minimum=60,
        maximum=86400,
    )
    return {
        'process_role': (os.getenv('NEXUS_PROCESS_ROLE', 'unspecified').strip() or 'unspecified')[:80],
        'pool_size': pool_size,
        'max_overflow': max_overflow,
        'pool_timeout_seconds': pool_timeout_seconds,
        'pool_recycle_seconds': pool_recycle_seconds,
        'max_connections_per_process': pool_size + max_overflow,
    }


def _pool_counter(pool: Any, name: str) -> int | None:
    value = getattr(pool, name, None)
    if not callable(value):
        return None
    try:
        result = int(value())
    except (TypeError, ValueError, RuntimeError):
        return None
    return max(result, 0)


def database_pool_snapshot() -> dict[str, Any]:
    """Return low-cardinality pool state without URLs, hosts or credentials."""
    pool = engine.pool
    configuration = database_pool_configuration() if db_url.drivername.startswith('postgresql') else {
        'process_role': (os.getenv('NEXUS_PROCESS_ROLE', 'unspecified').strip() or 'unspecified')[:80],
        'pool_size': None,
        'max_overflow': None,
        'pool_timeout_seconds': None,
        'pool_recycle_seconds': None,
        'max_connections_per_process': None,
    }
    checked_out = _pool_counter(pool, 'checkedout')
    checked_in = _pool_counter(pool, 'checkedin')
    overflow = _pool_counter(pool, 'overflow')
    configured_max = configuration.get('max_connections_per_process')
    utilization_percent = None
    if isinstance(configured_max, int) and configured_max > 0 and checked_out is not None:
        utilization_percent = round(checked_out * 100 / configured_max, 2)
    return {
        'schema': 'nexus.database-pool-snapshot.v1',
        'dialect': db_url.get_backend_name(),
        'pool_class': type(pool).__name__[:80],
        'configuration': configuration,
        'checked_out': checked_out,
        'checked_in': checked_in,
        'overflow': overflow,
        'utilization_percent': utilization_percent,
        'contains_connection_url': False,
    }


db_url = make_url(DATABASE_URL)
connect_args = {"check_same_thread": False, "timeout": 30} if db_url.drivername.startswith("sqlite") else {}
engine_kwargs = {"future": True, "echo": settings.database_echo, "pool_pre_ping": True}
if db_url.drivername.startswith("postgresql"):
    pool_configuration = database_pool_configuration()
    engine_kwargs.update(
        {
            "pool_size": pool_configuration['pool_size'],
            "max_overflow": pool_configuration['max_overflow'],
            "pool_timeout": pool_configuration['pool_timeout_seconds'],
            "pool_recycle": pool_configuration['pool_recycle_seconds'],
            "pool_use_lifo": True,
        }
    )
engine = create_engine(DATABASE_URL, connect_args=connect_args, **engine_kwargs)


@event.listens_for(engine, 'before_cursor_execute')
def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
    if not _db_query_timing_enabled():
        return
    context._nexusdesk_query_started_at = perf_counter()


@event.listens_for(engine, 'after_cursor_execute')
def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
    if not _db_query_timing_enabled():
        return
    started = getattr(context, '_nexusdesk_query_started_at', None)
    if started is None:
        return
    try:
        duration_ms = (perf_counter() - started) * 1000.0
        from .services.observability import record_db_query

        record_db_query(
            duration_ms,
            statement,
            slow_threshold_ms=_slow_query_ms(),
            request_id=_current_request_id.get(),
        )
    except Exception as exc:  # pragma: no cover - metrics must never break DB traffic
        try:
            from .services.observability import LOGGER

            LOGGER.warning('db_query_timing_failed', extra={'event_payload': {'error': type(exc).__name__}})
        except Exception:
            pass


if db_url.drivername.startswith("sqlite"):
    with engine.connect() as con:
        con.execute(text("PRAGMA journal_mode=WAL;"))
        con.execute(text("PRAGMA synchronous=NORMAL;"))

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_context():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# Register the canonical voice compliance projection with Base.metadata.
from . import voice_compliance_models as _voice_compliance_models  # noqa: E402,F401
