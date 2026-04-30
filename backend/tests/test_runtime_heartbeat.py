import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault('APP_ENV', 'development')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexusdesk_runtime_heartbeat.db')
os.environ.setdefault('ALLOW_DEV_AUTH', 'false')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.models import ServiceHeartbeat  # noqa: E402
from app.services.heartbeat_service import update_service_heartbeat  # noqa: E402
from app.utils.time import utc_now  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / 'runtime_heartbeat.db'
    engine = create_engine(f'sqlite:///{db_file}', connect_args={'check_same_thread': False}, future=True)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_worker_heartbeat_written(db_session):
    row = update_service_heartbeat(
        db_session,
        service_name='worker',
        instance_id='worker-main',
        status='ok',
        details={'processed': 2, 'outbound_processed': 1, 'background_jobs_processed': 1},
    )
    db_session.flush()
    assert row.service_name == 'worker'
    assert row.status == 'ok'
    assert row.details_json['processed'] == 2


def test_sync_daemon_heartbeat_written(db_session):
    row = update_service_heartbeat(
        db_session,
        service_name='openclaw_sync_daemon',
        instance_id='worker-openclaw-sync',
        status='ok',
        details={'processed': 3},
    )
    db_session.flush()
    assert row.service_name == 'openclaw_sync_daemon'
    assert row.details_json['processed'] == 3


def test_event_daemon_heartbeat_schema_is_compatible(db_session):
    row = update_service_heartbeat(
        db_session,
        service_name='openclaw_event_daemon',
        instance_id='openclaw-events-1',
        status='ok',
        details={'processed': 1},
    )
    db_session.flush()
    loaded = db_session.query(ServiceHeartbeat).filter(ServiceHeartbeat.service_name == 'openclaw_event_daemon').one()
    assert loaded.instance_id == 'openclaw-events-1'
    assert loaded.status == 'ok'


def test_stale_heartbeat_can_be_detected(db_session):
    row = update_service_heartbeat(db_session, service_name='worker', instance_id='worker-main', status='ok', details={})
    row.last_seen_at = utc_now() - timedelta(seconds=9999)
    db_session.flush()
    loaded = db_session.query(ServiceHeartbeat).filter(ServiceHeartbeat.service_name == 'worker').one()
    assert (utc_now() - loaded.last_seen_at).total_seconds() > 300
