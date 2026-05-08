import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault('APP_ENV', 'development')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/helpdesk_suite_openclaw_local_ops.db')
os.environ.setdefault('ALLOW_DEV_AUTH', 'false')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import hash_password  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.models import Team, User  # noqa: E402
from app.api import admin as admin_api  # noqa: E402
from app.services.openclaw_runtime_service import probe_openclaw_connectivity  # noqa: E402
from app.services import openclaw_runtime_service  # noqa: E402
from app.services import openclaw_mcp_client  # noqa: E402
from app.services.openclaw_mcp_client import OpenClawMCPClient, OpenClawMCPError, local_mcp_cli_allowed  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / 'suite.db'
    engine = create_engine(f"sqlite:///{db_file}", connect_args={'check_same_thread': False}, future=True)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def make_team(db_session, name='Support'):
    team = Team(name=name, team_type='support')
    db_session.add(team)
    db_session.flush()
    return team


def make_user(db_session, username, role, team):
    row = User(
        username=username,
        display_name=username.title(),
        email=f'{username}@example.com',
        password_hash=hash_password('pass123'),
        role=role,
        team_id=team.id,
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_probe_openclaw_connectivity_reports_bridge_when_client_works(monkeypatch):
    class DummyClient:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return None
        def conversations_list(self, **kwargs):
            return [{'session_key': 'sess-1'}]

    monkeypatch.setattr(openclaw_runtime_service, 'OpenClawMCPClient', DummyClient)
    result = probe_openclaw_connectivity()
    assert result.bridge_started is True
    assert result.conversations_tool_ok is True
    assert result.conversations_seen == 1
    assert result.sample_session_key == 'sess-1'


def test_admin_connectivity_check_requires_supervisor(db_session, monkeypatch):
    team = make_team(db_session)
    admin = make_user(db_session, 'admin-connect', UserRole.admin, team)
    lead = make_user(db_session, 'lead-connect', UserRole.lead, team)

    monkeypatch.setattr(admin_api, 'probe_openclaw_connectivity', lambda: type('Probe', (), {'model_dump': lambda self: {}})())

    with pytest.raises(Exception):
        admin_api.openclaw_connectivity_check(db_session, lead)

    monkeypatch.setattr(admin_api, 'probe_openclaw_connectivity', lambda: {'deployment_mode': 'local_gateway'})
    assert admin_api.openclaw_connectivity_check(db_session, admin) == {'deployment_mode': 'local_gateway'}


def test_local_openclaw_artifacts_are_present():
    env_example = (ROOT.parent / 'backend' / '.env.local-openclaw.example').read_text(encoding='utf-8')
    compose = (ROOT.parent / 'deploy' / 'docker-compose.local-openclaw.yml').read_text(encoding='utf-8')
    runtime = (ROOT.parent / 'webapp' / 'src' / 'routes' / 'runtime.tsx').read_text(encoding='utf-8')
    script = (ROOT.parent / 'scripts' / 'deploy' / 'bootstrap_local_openclaw.sh').read_text(encoding='utf-8')

    assert 'OPENCLAW_DEPLOYMENT_MODE=local_gateway' in env_example
    assert 'docker-compose.local-openclaw.yml' in script
    assert '检查 OpenClaw 联调' in runtime
    assert 'host.docker.internal:host-gateway' in compose


def test_local_mcp_cli_allowed_blocks_remote_gateway_without_fallback(monkeypatch):
    monkeypatch.setattr(openclaw_mcp_client.settings, 'openclaw_deployment_mode', 'remote_gateway')
    monkeypatch.setattr(openclaw_mcp_client.settings, 'openclaw_bridge_enabled', True)
    monkeypatch.setattr(openclaw_mcp_client.settings, 'openclaw_cli_fallback_enabled', False)

    assert local_mcp_cli_allowed() is False


def test_local_mcp_client_fails_before_subprocess_when_fallback_disabled(monkeypatch):
    monkeypatch.setattr(openclaw_mcp_client.settings, 'openclaw_deployment_mode', 'remote_gateway')
    monkeypatch.setattr(openclaw_mcp_client.settings, 'openclaw_bridge_enabled', True)
    monkeypatch.setattr(openclaw_mcp_client.settings, 'openclaw_cli_fallback_enabled', False)

    def fail_popen(*args, **kwargs):  # pragma: no cover - should never run
        raise AssertionError('subprocess should not be started')

    monkeypatch.setattr(openclaw_mcp_client.subprocess, 'Popen', fail_popen)

    with pytest.raises(OpenClawMCPError, match='local_openclaw_mcp_cli_disabled'):
        OpenClawMCPClient().start()


def test_local_mcp_cli_allowed_for_explicit_local_mode(monkeypatch):
    monkeypatch.setattr(openclaw_mcp_client.settings, 'openclaw_deployment_mode', 'local_gateway')
    monkeypatch.setattr(openclaw_mcp_client.settings, 'openclaw_bridge_enabled', False)
    monkeypatch.setattr(openclaw_mcp_client.settings, 'openclaw_cli_fallback_enabled', False)

    assert local_mcp_cli_allowed() is True
