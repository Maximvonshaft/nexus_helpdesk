from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .settings import get_settings

settings = get_settings()
DATABASE_URL = settings.database_url

db_url = make_url(DATABASE_URL)
connect_args = {"check_same_thread": False, "timeout": 30} if db_url.drivername.startswith("sqlite") else {}
engine_kwargs = {"future": True, "echo": settings.database_echo, "pool_pre_ping": True}
if db_url.drivername.startswith("postgresql"):
    engine_kwargs.update({"pool_size": 10, "max_overflow": 20, "pool_recycle": 1800})
engine = create_engine(DATABASE_URL, connect_args=connect_args, **engine_kwargs)

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
