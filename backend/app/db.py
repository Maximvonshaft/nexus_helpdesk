from contextlib import contextmanager
from contextvars import ContextVar
import os
from time import perf_counter

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


def _db_query_timing_enabled() -> bool:
    return os.getenv('DB_QUERY_TIMING_ENABLED', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}


def _slow_query_ms() -> float:
    try:
        return float(os.getenv('DB_SLOW_QUERY_MS', '500'))
    except ValueError:
        return 500.0


def _normalize_database_url(database_url: str) -> str:
    """Normalize PostgreSQL URLs to the repo-supported psycopg v3 driver.

    SQLAlchemy treats bare `postgresql://` as the legacy psycopg2 dialect. This
    repository ships `psycopg[binary]`, so non-production gates and deployments
    should work with either `postgresql://` or `postgresql+psycopg://` without
    requiring the old psycopg2 package.
    """
    parsed = make_url(database_url)
    if parsed.drivername == 'postgresql':
        return str(parsed.set(drivername='postgresql+psycopg'))
    return database_url


DATABASE_URL = _normalize_database_url(DATABASE_URL)
db_url = make_url(DATABASE_URL)
connect_args = {"check_same_thread": False, "timeout": 30} if db_url.drivername.startswith("sqlite") else {}
engine_kwargs = {"future": True, "echo": settings.database_echo, "pool_pre_ping": True}
if db_url.drivername.startswith("postgresql"):
    engine_kwargs.update({"pool_size": 10, "max_overflow": 20, "pool_recycle": 1800})
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
