import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault('APP_ENV', 'development')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/helpdesk_suite_next_phase_import.db')
os.environ.setdefault('ALLOW_DEV_AUTH', 'false')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import hash_password  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.models import AIConfigResource, Market, Team, User  # noqa: E402
from app.schemas import AIConfigPublishRequest, AIConfigResourceCreate, AIConfigResourceUpdate  # noqa: E402
from app.api import admin as admin_api  # noqa: E402
from app.api import lookups as lookups_api  # noqa: E402


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


def test_ai_config_can_be_created_published_and_read_via_lookups(db_session):
    team = make_team(db_session)
    admin = make_user(db_session, 'admin-next', UserRole.admin, team)
    market = Market(code='PH', name='Philippines', country_code='PH')
    db_session.add(market)
    db_session.commit()

    created = admin_api.create_ai_config(
        AIConfigResourceCreate(
            resource_key='support-persona',
            config_type='persona',
            name='客服人格',
            scope_type='global',
            draft_summary='统一客服语气',
            draft_content_json={'tone': 'clear'},
        ),
        db_session,
        admin,
    )
    published = admin_api.publish_ai_config(created.id, AIConfigPublishRequest(notes='initial publish'), db_session, admin)
    rows = lookups_api.list_ai_configs(config_type='persona', market_id=None, db=db_session, current_user=admin)

    assert published.version == 1
    assert rows and rows[0].published_version == 1
    assert rows[0].published_content_json == {'tone': 'clear'}


def test_ai_config_rollback_creates_new_published_version(db_session):
    team = make_team(db_session)
    admin = make_user(db_session, 'admin-roll', UserRole.admin, team)
    created = admin_api.create_ai_config(
        AIConfigResourceCreate(
            resource_key='delay-sop',
            config_type='sop',
            name='延误处理',
            scope_type='case_type',
            scope_value='Delivery Delay',
            draft_summary='第一版',
            draft_content_json={'steps': ['a']},
        ),
        db_session,
        admin,
    )
    admin_api.publish_ai_config(created.id, AIConfigPublishRequest(notes='v1'), db_session, admin)
    admin_api.update_ai_config(created.id, AIConfigResourceUpdate(draft_summary='第二版', draft_content_json={'steps': ['b']}), db_session, admin)
    admin_api.publish_ai_config(created.id, AIConfigPublishRequest(notes='v2'), db_session, admin)

    rolled = admin_api.rollback_ai_config(created.id, 1, AIConfigPublishRequest(notes='rollback'), db_session, admin)
    resource = db_session.query(AIConfigResource).filter(AIConfigResource.id == created.id).one()

    assert rolled.version == 3
    assert resource.published_content_json == {'steps': ['a']}
    assert resource.published_summary == '第一版'


def test_ai_config_management_is_limited_to_admin_or_manager(db_session):
    team = make_team(db_session)
    lead = make_user(db_session, 'lead-next', UserRole.lead, team)

    with pytest.raises(HTTPException) as exc:
        admin_api.create_ai_config(
            AIConfigResourceCreate(
                resource_key='lead-policy',
                config_type='policy',
                name='lead policy',
                draft_content_json={'approve': False},
            ),
            db_session,
            lead,
        )
    assert exc.value.status_code == 403


def test_webapp_has_ai_control_route_and_nav_entry():
    router = (ROOT.parent / 'webapp' / 'src' / 'router.tsx').read_text(encoding='utf-8')
    shell = (ROOT.parent / 'webapp' / 'src' / 'layouts' / 'AppShell.tsx').read_text(encoding='utf-8')
    route = (ROOT.parent / 'webapp' / 'src' / 'routes' / 'ai-control.tsx').read_text(encoding='utf-8')
    api = (ROOT.parent / 'webapp' / 'src' / 'lib' / 'api.ts').read_text(encoding='utf-8')

    assert 'AIControlRoute' in router
    assert "'/ai-control'" in shell
    assert '智能助手规则与知识配置' in route
    assert '/api/admin/ai-configs' in api
    assert '/api/lookups/ai-configs' in api
