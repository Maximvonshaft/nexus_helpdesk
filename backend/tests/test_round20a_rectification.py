import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault('APP_ENV', 'development')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/helpdesk_suite_round20a_import.db')
os.environ.setdefault('ALLOW_DEV_AUTH', 'false')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import hash_password  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.models import Market, MarketBulletin, Team, User  # noqa: E402
from app.api import lookups as lookups_api  # noqa: E402
from scripts import init_dev_db  # noqa: E402


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


def test_operator_lookup_endpoints_are_available_to_agent_role(db_session):
    team = make_team(db_session)
    agent = make_user(db_session, 'agent20a', UserRole.agent, team)
    market = Market(code='PH', name='Philippines', country_code='PH')
    db_session.add(market)
    db_session.flush()
    db_session.add(MarketBulletin(
        market_id=market.id,
        country_code='PH',
        title='延误提醒',
        body='当前末端处理稍有波动，请先安抚客户。',
        summary='请按统一口径解释时效波动。',
        category='delay',
        audience='operator',
        severity='warning',
        auto_inject_to_ai=True,
        is_active=True,
        created_by=agent.id,
    ))
    db_session.commit()

    markets = lookups_api.list_markets(db_session, agent)
    bulletins = lookups_api.list_operator_bulletins(db_session, agent)

    assert markets and markets[0].code == 'PH'
    assert bulletins and bulletins[0].title == '延误提醒'


def test_round20a_access_model_keeps_lead_out_of_ops_pages_and_bulletins_edit_mode():
    access = (ROOT.parent / 'webapp' / 'src' / 'lib' / 'access.ts').read_text(encoding='utf-8')
    app_shell = (ROOT.parent / 'webapp' / 'src' / 'layouts' / 'AppShell.tsx').read_text(encoding='utf-8')
    api = (ROOT.parent / 'webapp' / 'src' / 'lib' / 'api.ts').read_text(encoding='utf-8')

    assert "['admin', 'manager'].includes" in access
    assert "'lead'" not in access.split('includes', 1)[1].split('\n', 1)[0]
    assert 'canEditBulletins' in access
    assert "enabled: !!session.data && canSeeOps" in app_shell
    assert "/api/lookups/markets" in api
    assert "/api/lookups/bulletins" in api


def test_workspace_hides_session_key_from_customer_service_view():
    workspace = (ROOT.parent / 'webapp' / 'src' / 'routes' / 'workspace.tsx').read_text(encoding='utf-8')
    legacy = (ROOT.parent / 'frontend' / 'app.js').read_text(encoding='utf-8')

    assert '会话编号' not in workspace
    assert '来源状态' in workspace
    assert '会话编号' not in legacy
    assert '已绑定来信来源' in legacy


def test_init_dev_db_seeds_committed_demo_ticket_and_bulletin(tmp_path, monkeypatch):
    db_file = tmp_path / 'seed.db'
    engine = create_engine(f"sqlite:///{db_file}", connect_args={'check_same_thread': False}, future=True)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)

    monkeypatch.setattr(init_dev_db, 'SessionLocal', TestingSession)
    init_dev_db.seed_data()

    db = TestingSession()
    try:
        from app.models import Ticket  # local import to avoid circulars during module import

        assert db.query(Ticket).count() >= 1
        assert db.query(Market).count() >= 2
        assert db.query(MarketBulletin).count() >= 1
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
