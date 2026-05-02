import importlib.util
import os
import sys
from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

# Stable test env before app imports.
os.environ.setdefault('APP_ENV', 'development')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/helpdesk_suite_round24_import.db')
os.environ.setdefault('ALLOW_DEV_AUTH', 'false')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import hash_password, hash_secret  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import JobStatus, NoteVisibility, SourceChannel, TicketPriority, TicketSource, UserRole  # noqa: E402
from app.models import (  # noqa: E402
    AuthThrottleEntry,
    BackgroundJob,
    IntegrationClient,
    IntegrationRequestLog,
    Market,
    OpenClawAttachmentReference,
    OpenClawConversationLink,
    OpenClawSyncCursor,
    OpenClawTranscriptMessage,
    Team,
    Ticket,
    User,
    UserCapabilityOverride,
)
from app.schemas import AIIntakeCreate, CommentCreate, LiteAIIntakeRequest, LiteAssignRequest, TicketCreate  # noqa: E402
from app.api import auth as auth_api  # noqa: E402
from app.api import integration as integration_api  # noqa: E402
from app.api import lookups as lookups_api  # noqa: E402
from app.api import tickets as tickets_api  # noqa: E402
from app.services.integration_auth import authenticate_integration_client  # noqa: E402
from app.services.lite_service import assign_lite_case, get_lite_case, save_ai_intake_lite  # noqa: E402
from app.services.ticket_service import add_ai_intake, add_comment, create_ticket  # noqa: E402
from app.settings import Settings  # noqa: E402
from app.utils import client_ip as client_ip_utils  # noqa: E402
from app.services import background_jobs, openclaw_bridge  # noqa: E402
from app.services.background_jobs import ATTACHMENT_PERSIST_JOB, OPENCLAW_SYNC_JOB, claim_pending_jobs, dispatch_pending_background_jobs, dispatch_pending_sync_jobs, enqueue_background_job  # noqa: E402
from scripts import run_worker  # noqa: E402


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


def make_team(db_session, name='Ops Team'):
    team = Team(name=name, team_type='support')
    db_session.add(team)
    db_session.flush()
    return team


def make_user(db_session, username, role=UserRole.agent, team=None):
    user = User(
        username=username,
        display_name=username.title(),
        email=f'{username}@example.com',
        password_hash=hash_password('pass123'),
        role=role,
        team_id=team.id if team else None,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def make_ticket(db_session, actor, *, team=None, assignee=None, contact='+1000001'):
    ticket = create_ticket(
        db_session,
        TicketCreate(
            title='Parcel delayed',
            description='Customer reports a delay',
            source=TicketSource.manual,
            source_channel=SourceChannel.whatsapp,
            priority=TicketPriority.medium,
            team_id=(team.id if team else actor.team_id),
            assignee_id=(assignee.id if assignee else None),
            customer=None,
            preferred_reply_channel='whatsapp',
            preferred_reply_contact=contact,
            source_chat_id=contact,
            issue_summary='Delay complaint',
            customer_request='Where is my parcel?',
        ),
        actor,
    )
    db_session.commit()
    db_session.refresh(ticket)
    return ticket


def test_auth_login_success_clears_throttle_entry(db_session, monkeypatch):
    team = make_team(db_session)
    user = make_user(db_session, 'lead1', UserRole.lead, team)
    db_session.add(AuthThrottleEntry(throttle_key='lead1|127.0.0.1', fail_count=5))
    db_session.commit()

    monkeypatch.setattr(client_ip_utils, 'get_settings', lambda: SimpleNamespace(trusted_proxy_ips=[]))
    request = SimpleNamespace(client=SimpleNamespace(host='127.0.0.1'), headers={})
    response = auth_api.login(SimpleNamespace(username='lead1', password='pass123'), request, db_session)

    assert response.access_token
    assert db_session.query(AuthThrottleEntry).count() == 0


def test_auth_login_uses_forwarded_ip_from_trusted_proxy(db_session, monkeypatch):
    team = make_team(db_session)
    make_user(db_session, 'lead-forwarded', UserRole.lead, team)
    db_session.add(AuthThrottleEntry(throttle_key='lead-forwarded|203.0.113.10', fail_count=3))
    db_session.commit()

    monkeypatch.setattr(client_ip_utils, 'get_settings', lambda: SimpleNamespace(trusted_proxy_ips=['127.0.0.1/32']))
    request = SimpleNamespace(client=SimpleNamespace(host='127.0.0.1'), headers={'x-forwarded-for': '203.0.113.10, 127.0.0.1'})

    response = auth_api.login(SimpleNamespace(username='lead-forwarded', password='pass123'), request, db_session)

    assert response.access_token
    assert db_session.query(AuthThrottleEntry).count() == 0


def test_integration_task_commits_ticket_log_and_client_usage(db_session):
    team = make_team(db_session)
    lead = make_user(db_session, 'lead2', UserRole.lead, team)
    row = IntegrationClient(
        name='partner-a',
        key_id='kid-1',
        secret_hash=hash_secret('secret-1'),
        scopes_csv='profile.read,task.write',
        rate_limit_per_minute=30,
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()

    client = authenticate_integration_client(db_session, x_client_key_id='kid-1', x_client_key='secret-1', x_api_key=None)
    payload = integration_api.IntegrationTaskRequest(contact_id='+15550001', summary='Manual escalation needed', channel='whatsapp')
    response = integration_api.nexusdesk_escalate_task(payload, SimpleNamespace(), db_session, client, 'idem-1')

    db_session.expire_all()
    saved_client = db_session.query(IntegrationClient).filter_by(key_id='kid-1').one()
    assert response['status'] == 'created'
    assert db_session.query(Ticket).count() == 1
    assert db_session.query(IntegrationRequestLog).filter_by(endpoint='integration.task').count() == 1
    assert saved_client.last_used_at is not None


def test_integration_profile_commits_request_log(db_session):
    team = make_team(db_session)
    lead = make_user(db_session, 'lead3', UserRole.lead, team)
    make_ticket(db_session, lead, team=team, contact='+15550002')
    row = IntegrationClient(
        name='partner-b',
        key_id='kid-2',
        secret_hash=hash_secret('secret-2'),
        scopes_csv='profile.read,task.write',
        rate_limit_per_minute=30,
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()

    client = authenticate_integration_client(db_session, x_client_key_id='kid-2', x_client_key='secret-2', x_api_key=None)
    response = integration_api.nexusdesk_customer_profile('+15550002', 'whatsapp', db_session, client)

    assert response['found'] is True
    assert db_session.query(IntegrationRequestLog).filter_by(endpoint='integration.profile').count() == 1


@pytest.mark.parametrize('visibility', [NoteVisibility.external, NoteVisibility.internal])
def test_auditor_cannot_write_comments(db_session, visibility):
    team = make_team(db_session)
    lead = make_user(db_session, 'lead4', UserRole.lead, team)
    auditor = make_user(db_session, 'auditor1', UserRole.auditor, team)
    ticket = make_ticket(db_session, lead, team=team)

    with pytest.raises(HTTPException) as exc:
        add_comment(db_session, ticket.id, CommentCreate(body='not allowed', visibility=visibility), auditor)
    assert exc.value.status_code == 403


def test_capability_override_is_enforced_for_ticket_reads(db_session):
    team = make_team(db_session)
    lead = make_user(db_session, 'lead5', UserRole.lead, team)
    agent = make_user(db_session, 'agent1', UserRole.agent, team)
    ticket = make_ticket(db_session, lead, team=team, assignee=agent)
    db_session.add(UserCapabilityOverride(user_id=agent.id, capability='ticket.read', allowed=False))
    db_session.commit()

    with pytest.raises(HTTPException) as lite_exc:
        get_lite_case(db_session, ticket.id, agent)
    with pytest.raises(HTTPException) as api_exc:
        tickets_api.get_ticket_endpoint(ticket.id, db_session, agent)

    assert lite_exc.value.status_code == 403
    assert api_exc.value.status_code == 403


def test_capability_override_is_enforced_for_lite_assign(db_session):
    team = make_team(db_session)
    lead = make_user(db_session, 'lead6', UserRole.lead, team)
    agent = make_user(db_session, 'agent2', UserRole.agent, team)
    assignee = make_user(db_session, 'agent3', UserRole.agent, team)
    ticket = make_ticket(db_session, lead, team=team, assignee=agent)
    db_session.add(UserCapabilityOverride(user_id=agent.id, capability='ticket.assign', allowed=True))
    db_session.commit()

    case = assign_lite_case(db_session, ticket.id, LiteAssignRequest(assignee_id=assignee.id, team_id=team.id), agent)

    assert case.assigned_to == assignee.display_name


def test_lookups_are_team_scoped_for_agents(db_session):
    team1 = make_team(db_session, 'Team A')
    team2 = make_team(db_session, 'Team B')
    agent = make_user(db_session, 'agent4', UserRole.agent, team1)
    teammate = make_user(db_session, 'agent5', UserRole.agent, team1)
    other_team_user = make_user(db_session, 'agent6', UserRole.agent, team2)
    db_session.commit()

    users = lookups_api.list_users(db_session, agent)
    teams = lookups_api.list_teams(db_session, agent)

    usernames = {user.username for user in users}
    assert usernames == {'agent4', 'agent5'}
    assert [team.name for team in teams] == ['Team A']


def test_settings_reject_placeholder_secret_in_production(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('SECRET_KEY', 'change-me')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@db/app')
    monkeypatch.setenv('ALLOWED_ORIGINS', 'https://app.example.com')
    monkeypatch.setenv('AUTO_INIT_DB', 'false')
    monkeypatch.setenv('SEED_DEMO_DATA', 'false')
    monkeypatch.setenv('ALLOW_DEV_AUTH', 'false')
    monkeypatch.setenv('ALLOW_LEGACY_INTEGRATION_API_KEY', 'false')
    with pytest.raises(RuntimeError):
        Settings()


def test_worker_skips_outbound_dispatch_when_disabled(monkeypatch):
    @contextmanager
    def dummy_db_context():
        yield SimpleNamespace()

    monkeypatch.setattr(run_worker.settings, 'enable_outbound_dispatch', False)
    monkeypatch.setattr(run_worker, 'db_context', dummy_db_context)
    monkeypatch.setattr(run_worker, 'dispatch_pending_messages', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('outbound dispatch should not run')))
    monkeypatch.setattr(run_worker, 'dispatch_pending_background_jobs', lambda *args, **kwargs: [])
    monkeypatch.setattr(run_worker, 'record_queue_snapshot', lambda *args, **kwargs: None)
    monkeypatch.setattr(run_worker, 'record_worker_poll', lambda *args, **kwargs: None)
    monkeypatch.setattr(run_worker, 'record_worker_result', lambda *args, **kwargs: None)
    monkeypatch.setattr(run_worker, 'log_event', lambda *args, **kwargs: None)

    assert run_worker.run_once('worker-test') == 0


def test_worker_runs_openclaw_inbound_auto_sync_when_enabled(monkeypatch):
    calls = []

    @contextmanager
    def dummy_db_context():
        yield SimpleNamespace()

    monkeypatch.setattr(run_worker.settings, 'enable_outbound_dispatch', False)
    monkeypatch.setattr(run_worker.settings, 'openclaw_sync_enabled', True)
    monkeypatch.setattr(run_worker.settings, 'openclaw_inbound_auto_sync_enabled', True)
    monkeypatch.setattr(run_worker, 'db_context', dummy_db_context)
    monkeypatch.setattr(run_worker, 'dispatch_pending_messages', lambda *args, **kwargs: [])
    monkeypatch.setattr(run_worker, 'dispatch_pending_background_jobs', lambda *args, **kwargs: [])
    monkeypatch.setattr(run_worker, 'sync_openclaw_inbound_conversations_once', lambda *args, **kwargs: calls.append(kwargs.get('source')) or {'synced_conversations': 2, 'conversations_seen': 2, 'tickets_created': 1, 'messages_inserted': 3, 'unresolved_events': 0})
    monkeypatch.setattr(run_worker, 'record_queue_snapshot', lambda *args, **kwargs: None)
    monkeypatch.setattr(run_worker, 'record_worker_poll', lambda *args, **kwargs: None)
    monkeypatch.setattr(run_worker, 'record_worker_result', lambda *args, **kwargs: None)
    monkeypatch.setattr(run_worker, 'log_event', lambda *args, **kwargs: None)

    assert run_worker.run_once('worker-test') == 2
    assert calls == ['default']


def test_sync_openclaw_conversation_reuses_single_mcp_client(db_session, monkeypatch):
    team = make_team(db_session)
    lead = make_user(db_session, 'lead7', UserRole.lead, team)
    ticket = make_ticket(db_session, lead, team=team)

    class FakeClient:
        instances = 0

        def __init__(self):
            type(self).instances += 1

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def conversation_get(self, session_key):
            return {'session_key': session_key, 'channel': 'whatsapp', 'recipient': '+15550003'}

        def messages_read(self, session_key, limit=50):
            return [{'id': 'msg-1', 'role': 'user', 'author': 'customer', 'text': 'hello'}]

        def attachments_fetch(self, message_id):
            return {'attachments': [{'id': 'att-1', 'contentType': 'image/png', 'filename': 'proof.png'}]}

    monkeypatch.setattr(openclaw_bridge, 'OpenClawMCPClient', FakeClient)

    result = openclaw_bridge.sync_openclaw_conversation(db_session, ticket_id=ticket.id, session_key='sess-1', limit=10)
    db_session.commit()

    assert result.linked_ticket_id == ticket.id
    assert FakeClient.instances == 1
    assert db_session.query(OpenClawAttachmentReference).count() == 1


def test_sync_openclaw_conversation_generates_stable_synthetic_message_ids(db_session, monkeypatch):
    team = make_team(db_session)
    lead = make_user(db_session, 'lead-synth', UserRole.lead, team)
    ticket = make_ticket(db_session, lead, team=team)

    monkeypatch.setattr(openclaw_bridge.settings, 'openclaw_bridge_enabled', True)
    monkeypatch.setattr(openclaw_bridge, 'read_openclaw_bridge_conversation', lambda *args, **kwargs: (
        {'sessionKey': 'sess-synth', 'route': {'channel': 'telegram', 'recipient': 'telegram:customer-1'}},
        [{'role': 'user', 'author': 'customer', 'text': 'hello from telegram', 'createdAt': '2026-05-01T09:00:00+00:00'}],
    ))
    monkeypatch.setattr(openclaw_bridge, 'fetch_openclaw_bridge_attachments', lambda *args, **kwargs: [])

    openclaw_bridge.sync_openclaw_conversation(db_session, ticket_id=ticket.id, session_key='sess-synth', limit=10)
    openclaw_bridge.sync_openclaw_conversation(db_session, ticket_id=ticket.id, session_key='sess-synth', limit=10)
    db_session.commit()

    rows = db_session.query(OpenClawTranscriptMessage).filter_by(session_key='sess-synth').all()
    assert len(rows) == 1
    assert rows[0].message_id.startswith('synth-')


def test_sync_openclaw_inbound_conversations_auto_creates_ticket_and_records_unresolved(db_session, monkeypatch):
    team = make_team(db_session)
    make_user(db_session, 'lead-discovery', UserRole.lead, team)

    monkeypatch.setattr(openclaw_bridge.settings, 'openclaw_sync_enabled', True)
    monkeypatch.setattr(openclaw_bridge.settings, 'openclaw_inbound_auto_sync_enabled', True)
    monkeypatch.setattr(openclaw_bridge.settings, 'openclaw_inbound_auto_sync_interval_seconds', 0)
    monkeypatch.setattr(openclaw_bridge.settings, 'openclaw_inbound_sync_limit', 10)
    monkeypatch.setattr(openclaw_bridge.settings, 'openclaw_inbound_sync_include_groups', False)
    monkeypatch.setattr(openclaw_bridge.settings, 'openclaw_inbound_sync_message_limit', 20)
    monkeypatch.setattr(openclaw_bridge.settings, 'openclaw_bridge_enabled', True)
    monkeypatch.setattr(openclaw_bridge, 'list_openclaw_conversations', lambda **kwargs: {
        'conversations': [
            {'sessionKey': 'sess-valid', 'route': {'channel': 'telegram', 'recipient': 'telegram:customer-42', 'accountId': 'default'}},
            {'sessionKey': 'sess-group', 'route': {'channel': 'whatsapp', 'recipient': '120363@g.us', 'accountId': 'default'}},
            {'sessionKey': 'sess-bad', 'route': {'channel': 'telegram', 'accountId': 'default'}},
        ]
    })

    def fake_read(session_key, limit=50):
        if session_key == 'sess-valid':
            return (
                {'sessionKey': 'sess-valid', 'route': {'channel': 'telegram', 'recipient': 'telegram:customer-42', 'accountId': 'default'}},
                [{'id': 'msg-1', 'role': 'user', 'author': 'customer', 'text': 'Need help with parcel ETA'}],
            )
        if session_key == 'sess-group':
            return (
                {'sessionKey': 'sess-group', 'route': {'channel': 'whatsapp', 'recipient': '120363@g.us', 'accountId': 'default'}},
                [{'id': 'msg-g', 'role': 'user', 'author': 'customer', 'text': 'group chat'}],
            )
        return ({'sessionKey': session_key, 'route': {'channel': 'telegram'}}, [])

    monkeypatch.setattr(openclaw_bridge, 'read_openclaw_bridge_conversation', fake_read)
    monkeypatch.setattr(openclaw_bridge, 'fetch_openclaw_bridge_attachments', lambda *args, **kwargs: [])

    summary = openclaw_bridge.sync_openclaw_inbound_conversations_once(db_session, source='default', force=True)
    db_session.commit()

    assert summary['synced_conversations'] == 1
    assert summary['conversations_seen'] == 3
    assert summary['conversations_skipped'] == 2
    assert summary['tickets_created'] == 1
    assert summary['links_created'] == 1
    assert summary['messages_inserted'] == 1
    assert summary['unresolved_events'] == 1
    ticket = db_session.query(Ticket).one()
    assert ticket.source == TicketSource.user_message
    assert ticket.source_channel == SourceChannel.telegram
    assert ticket.source_chat_id == 'telegram:customer-42'
    assert ticket.preferred_reply_channel == 'telegram'
    assert ticket.preferred_reply_contact == 'telegram:customer-42'
    assert ticket.last_customer_message == 'Need help with parcel ETA'
    assert db_session.query(OpenClawConversationLink).filter_by(session_key='sess-valid').count() == 1
    assert db_session.query(OpenClawTranscriptMessage).filter_by(session_key='sess-valid').count() == 1
    assert db_session.query(OpenClawConversationLink).filter_by(session_key='sess-group').count() == 0
    unresolved = db_session.query(openclaw_bridge.OpenClawUnresolvedEvent).filter_by(session_key='sess-bad').one()
    assert unresolved.last_error == 'Missing recipient in conversations-list payload'


def test_sync_openclaw_inbound_parses_session_key_variants_and_reuses_existing_ticket(db_session, monkeypatch):
    team = make_team(db_session)
    lead = make_user(db_session, 'lead-existing', UserRole.lead, team)
    ticket = make_ticket(db_session, lead, team=team, contact='+15558889999')

    monkeypatch.setattr(openclaw_bridge.settings, 'openclaw_sync_enabled', True)
    monkeypatch.setattr(openclaw_bridge.settings, 'openclaw_inbound_auto_sync_enabled', True)
    monkeypatch.setattr(openclaw_bridge.settings, 'openclaw_inbound_auto_sync_interval_seconds', 0)
    monkeypatch.setattr(openclaw_bridge.settings, 'openclaw_inbound_sync_limit', 10)
    monkeypatch.setattr(openclaw_bridge.settings, 'openclaw_inbound_sync_message_limit', 20)
    monkeypatch.setattr(openclaw_bridge.settings, 'openclaw_inbound_sync_include_groups', False)
    monkeypatch.setattr(openclaw_bridge.settings, 'openclaw_bridge_enabled', True)
    monkeypatch.setattr(openclaw_bridge, 'list_openclaw_conversations', lambda **kwargs: {
        'conversations': [
            {'session_key': 'sess-existing', 'route': {'channel': 'whatsapp', 'recipient': '+15558889999', 'accountId': 'default'}},
        ]
    })
    monkeypatch.setattr(openclaw_bridge, 'read_openclaw_bridge_conversation', lambda session_key, limit=50: (
        {'session_key': session_key, 'route': {'channel': 'whatsapp', 'recipient': '+15558889999', 'accountId': 'default'}},
        [{'message_id': 'msg-existing', 'role': 'user', 'author': 'customer', 'text': 'Need update'}],
    ))
    monkeypatch.setattr(openclaw_bridge, 'fetch_openclaw_bridge_attachments', lambda *args, **kwargs: [])

    summary = openclaw_bridge.sync_openclaw_inbound_conversations_once(db_session, source='default', force=True)
    db_session.commit()

    assert summary['tickets_created'] == 0
    assert summary['links_created'] == 1
    assert db_session.query(Ticket).count() == 1
    link = db_session.query(OpenClawConversationLink).filter_by(session_key='sess-existing').one()
    assert link.ticket_id == ticket.id
    assert db_session.query(OpenClawTranscriptMessage).filter_by(session_key='sess-existing', message_id='msg-existing').count() == 1


def test_inbound_sync_never_calls_send_message_paths(db_session, monkeypatch):
    team = make_team(db_session)
    make_user(db_session, 'lead-safe', UserRole.lead, team)

    monkeypatch.setattr(openclaw_bridge.settings, 'openclaw_sync_enabled', True)
    monkeypatch.setattr(openclaw_bridge.settings, 'openclaw_inbound_auto_sync_enabled', True)
    monkeypatch.setattr(openclaw_bridge.settings, 'openclaw_inbound_auto_sync_interval_seconds', 0)
    monkeypatch.setattr(openclaw_bridge.settings, 'openclaw_inbound_sync_limit', 10)
    monkeypatch.setattr(openclaw_bridge.settings, 'openclaw_inbound_sync_message_limit', 20)
    monkeypatch.setattr(openclaw_bridge.settings, 'openclaw_bridge_enabled', True)
    monkeypatch.setattr(openclaw_bridge, 'dispatch_via_openclaw_bridge', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('send-message should not be called')))
    monkeypatch.setattr(openclaw_bridge, 'dispatch_via_openclaw_mcp', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('messages_send should not be called')))
    monkeypatch.setattr(openclaw_bridge, 'dispatch_via_openclaw_cli', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('cli send should not be called')))
    monkeypatch.setattr(openclaw_bridge, 'list_openclaw_conversations', lambda **kwargs: {
        'conversations': [
            {'sessionKey': 'sess-safe', 'route': {'channel': 'telegram', 'recipient': 'telegram:customer-safe', 'accountId': 'default'}},
        ]
    })
    monkeypatch.setattr(openclaw_bridge, 'read_openclaw_bridge_conversation', lambda session_key, limit=50: (
        {'sessionKey': session_key, 'route': {'channel': 'telegram', 'recipient': 'telegram:customer-safe', 'accountId': 'default'}},
        [{'id': 'msg-safe', 'role': 'user', 'author': 'customer', 'text': 'hello safe path'}],
    ))
    monkeypatch.setattr(openclaw_bridge, 'fetch_openclaw_bridge_attachments', lambda *args, **kwargs: [])

    summary = openclaw_bridge.sync_openclaw_inbound_conversations_once(db_session, source='default', force=True)
    db_session.commit()

    assert summary['synced_conversations'] == 1
    assert db_session.query(BackgroundJob).filter(BackgroundJob.job_type == 'auto_reply.send_update').count() == 0


def test_integration_task_missing_idempotency_is_audited_and_persists_last_used(db_session):
    team = make_team(db_session)
    make_user(db_session, 'lead8', UserRole.lead, team)
    row = IntegrationClient(
        name='partner-c',
        key_id='kid-3',
        secret_hash=hash_secret('secret-3'),
        scopes_csv='profile.read,task.write',
        rate_limit_per_minute=30,
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()

    client = authenticate_integration_client(db_session, x_client_key_id='kid-3', x_client_key='secret-3', x_api_key=None)
    payload = integration_api.IntegrationTaskRequest(contact_id='+15550005', summary='Need help', channel='whatsapp')

    with pytest.raises(HTTPException) as exc:
        integration_api.nexusdesk_escalate_task(payload, SimpleNamespace(), db_session, client, None)

    assert exc.value.status_code == 400
    db_session.expire_all()
    saved_client = db_session.query(IntegrationClient).filter_by(key_id='kid-3').one()
    log = db_session.query(IntegrationRequestLog).filter_by(endpoint='integration.task').one()
    assert saved_client.last_used_at is not None
    assert log.status_code == 400
    assert log.error_code == 'bad_request'


def test_integration_task_rate_limit_is_audited(db_session):
    team = make_team(db_session)
    make_user(db_session, 'lead9', UserRole.lead, team)
    row = IntegrationClient(
        name='partner-d',
        key_id='kid-4',
        secret_hash=hash_secret('secret-4'),
        scopes_csv='profile.read,task.write',
        rate_limit_per_minute=1,
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()

    client = authenticate_integration_client(db_session, x_client_key_id='kid-4', x_client_key='secret-4', x_api_key=None)
    payload = integration_api.IntegrationTaskRequest(contact_id='+15550006', summary='First task', channel='whatsapp')
    integration_api.nexusdesk_escalate_task(payload, SimpleNamespace(), db_session, client, 'idem-ok')

    payload2 = integration_api.IntegrationTaskRequest(contact_id='+15550007', summary='Second task', channel='whatsapp')
    with pytest.raises(HTTPException) as exc:
        integration_api.nexusdesk_escalate_task(payload2, SimpleNamespace(), db_session, client, 'idem-rate-limit')

    assert exc.value.status_code == 429
    logs = db_session.query(IntegrationRequestLog).filter_by(endpoint='integration.task').order_by(IntegrationRequestLog.id.asc()).all()
    assert [log.status_code for log in logs] == [200, 429]
    assert logs[-1].error_code == 'rate_limited'




def test_openclaw_attachment_url_fetch_is_disabled_by_default(monkeypatch):
    called = {'value': False}

    def fake_urlopen(*args, **kwargs):
        called['value'] = True
        raise AssertionError('urlopen should not be called when remote fetch is disabled')

    monkeypatch.setattr(openclaw_bridge.settings, 'openclaw_attachment_url_fetch_enabled', False)
    monkeypatch.setattr(openclaw_bridge.urllib.request, 'urlopen', fake_urlopen)

    payload, media_type, filename = openclaw_bridge._try_extract_attachment_bytes({
        'downloadUrl': 'https://files.example.com/proof.png',
        'contentType': 'image/png',
        'filename': 'proof.png',
    })

    assert payload is None
    assert media_type is None
    assert filename is None
    assert called['value'] is False


def test_claim_pending_jobs_can_filter_job_types(db_session):
    sync_job = enqueue_background_job(db_session, queue_name='openclaw_sync', job_type=OPENCLAW_SYNC_JOB, payload={'ticket_id': 1, 'session_key': 's1'})
    attachment_job = enqueue_background_job(db_session, queue_name='openclaw_attachment', job_type=ATTACHMENT_PERSIST_JOB, payload={'attachment_ref_id': 1})
    db_session.commit()

    claimed = claim_pending_jobs(db_session, worker_id='sync-worker', job_types=[OPENCLAW_SYNC_JOB])

    assert [job.job_type for job in claimed] == [OPENCLAW_SYNC_JOB]
    db_session.refresh(sync_job)
    db_session.refresh(attachment_job)
    assert sync_job.status == JobStatus.processing
    assert attachment_job.status == JobStatus.pending


def test_dispatch_pending_sync_jobs_only_processes_sync_queue(db_session, monkeypatch):
    sync_calls = []
    attachment_calls = []
    sync_job = enqueue_background_job(db_session, queue_name='openclaw_sync', job_type=OPENCLAW_SYNC_JOB, payload={'ticket_id': 1, 'session_key': 'sess-1'})
    enqueue_background_job(db_session, queue_name='openclaw_attachment', job_type=ATTACHMENT_PERSIST_JOB, payload={'attachment_ref_id': 9})
    db_session.commit()

    def fake_sync(*args, **kwargs):
        sync_calls.append(kwargs.get('session_key'))
        return SimpleNamespace(linked_ticket_id=1)

    def fake_persist(*args, **kwargs):
        attachment_calls.append(kwargs.get('attachment_ref'))
        return None

    class DummyClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(background_jobs.openclaw_client_factory, 'get_openclaw_runtime_client', lambda: DummyClient())
    monkeypatch.setattr(background_jobs.openclaw_bridge, 'sync_openclaw_conversation', fake_sync)
    monkeypatch.setattr(background_jobs.openclaw_bridge, 'persist_openclaw_attachment_reference', fake_persist, raising=False)
    monkeypatch.setattr(background_jobs.settings, 'openclaw_sync_enabled', True)

    processed = dispatch_pending_sync_jobs(db_session, worker_id='sync-only')

    assert len(processed) == 1
    assert processed[0].id == sync_job.id
    assert sync_calls == ['sess-1']
    assert attachment_calls == []


def test_dispatch_pending_background_jobs_excludes_sync_jobs(db_session, monkeypatch):
    team = make_team(db_session)
    lead = make_user(db_session, 'lead-bg', UserRole.lead, team)
    ticket = make_ticket(db_session, lead, team=team)
    link = OpenClawConversationLink(ticket_id=ticket.id, session_key='sess-bg', channel='whatsapp', recipient=ticket.preferred_reply_contact)
    db_session.add(link)
    db_session.flush()
    transcript = OpenClawTranscriptMessage(
        conversation_id=link.id,
        ticket_id=ticket.id,
        session_key='sess-bg',
        message_id='msg-bg-1',
        role='user',
        author_name='customer',
        body_text='hello',
    )
    db_session.add(transcript)
    db_session.flush()
    attachment_ref = OpenClawAttachmentReference(
        ticket_id=ticket.id,
        conversation_id=link.id,
        transcript_message_id=transcript.id,
        remote_attachment_id='att-1',
        metadata_json={},
    )
    db_session.add(attachment_ref)
    db_session.flush()
    enqueue_background_job(db_session, queue_name='openclaw_sync', job_type=OPENCLAW_SYNC_JOB, payload={'ticket_id': ticket.id, 'session_key': 'sess-keep'})
    attachment_job = enqueue_background_job(db_session, queue_name='openclaw_attachment', job_type=ATTACHMENT_PERSIST_JOB, payload={'attachment_ref_id': attachment_ref.id})
    db_session.commit()

    def fake_persist(db, *, attachment_ref):
        attachment_ref.storage_status = 'captured'
        return None

    monkeypatch.setattr(sys.modules['app.services.background_jobs'].settings, 'openclaw_sync_enabled', False)
    monkeypatch.setattr(openclaw_bridge, 'persist_openclaw_attachment_reference', fake_persist, raising=False)

    processed = dispatch_pending_background_jobs(db_session, worker_id='main-worker')

    assert [job.job_type for job in processed] == [ATTACHMENT_PERSIST_JOB]
    db_session.refresh(attachment_job)
    assert attachment_job.status == JobStatus.done
    sync_job = db_session.query(BackgroundJob).filter_by(job_type=OPENCLAW_SYNC_JOB).one()
    assert sync_job.status == JobStatus.pending


def test_metrics_endpoint_returns_503_when_enabled_without_token(monkeypatch):
    from app import main as app_main

    monkeypatch.setattr(app_main.settings, 'metrics_enabled', True)
    monkeypatch.setattr(app_main.settings, 'metrics_token', None)
    client = TestClient(app_main.app)

    response = client.get('/metrics')
    assert response.status_code == 503
    assert response.json()['detail'] == 'metrics misconfigured'


def test_readyz_does_not_leak_exception_details(monkeypatch):
    from app import main as app_main

    class BrokenConn:
        def __enter__(self):
            raise RuntimeError('database exploded with secret details')
        def __exit__(self, exc_type, exc, tb):
            return False

    class BrokenEngine:
        def connect(self):
            return BrokenConn()

    monkeypatch.setattr(app_main, 'engine', BrokenEngine())
    client = TestClient(app_main.app)

    response = client.get('/readyz')
    assert response.status_code == 503
    assert response.json() == {'status': 'not_ready', 'database': 'error'}


def test_metrics_require_token_in_production(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('SECRET_KEY', 'super-strong-secret-value')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@db/app')
    monkeypatch.setenv('ALLOWED_ORIGINS', 'https://app.example.com')
    monkeypatch.setenv('AUTO_INIT_DB', 'false')
    monkeypatch.setenv('SEED_DEMO_DATA', 'false')
    monkeypatch.setenv('ALLOW_DEV_AUTH', 'false')
    monkeypatch.setenv('ALLOW_LEGACY_INTEGRATION_API_KEY', 'false')
    monkeypatch.setenv('METRICS_ENABLED', 'true')
    monkeypatch.delenv('METRICS_TOKEN', raising=False)
    with pytest.raises(RuntimeError):
        Settings()



def test_metrics_endpoint_requires_matching_token(monkeypatch):
    from app import main as app_main

    monkeypatch.setattr(app_main.settings, 'metrics_enabled', True)
    monkeypatch.setattr(app_main.settings, 'metrics_token', 'metrics-secret')
    client = TestClient(app_main.app)

    missing = client.get('/metrics')
    assert missing.status_code == 401

    ok = client.get('/metrics', headers={'X-Metrics-Token': 'metrics-secret'})
    assert ok.status_code == 200
    assert 'text/plain' in ok.headers['content-type']


def test_consume_openclaw_events_reuses_single_mcp_client(db_session, monkeypatch):
    team = make_team(db_session)
    lead = make_user(db_session, 'lead10', UserRole.lead, team)
    ticket = make_ticket(db_session, lead, team=team)
    link = OpenClawConversationLink(ticket_id=ticket.id, session_key='sess-event', channel='whatsapp', recipient='+15550008')
    db_session.add(link)
    db_session.add(OpenClawSyncCursor(source='default', cursor_value='cursor-0'))
    db_session.commit()

    class FakeClient:
        instances = 0

        def __init__(self):
            type(self).instances += 1

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def events_wait(self, cursor=None, timeout_seconds=None):
            return {'cursor': 'cursor-1', 'events': [{'type': 'message', 'sessionKey': 'sess-event'}]}

        def conversation_get(self, session_key):
            return {'session_key': session_key, 'channel': 'whatsapp', 'recipient': '+15550008'}

        def messages_read(self, session_key, limit=50):
            return [{'id': 'msg-evt-1', 'role': 'user', 'author': 'customer', 'text': 'hello'}]

        def attachments_fetch(self, message_id):
            return {'attachments': []}

    monkeypatch.setattr(openclaw_bridge, 'OpenClawMCPClient', FakeClient)

    processed = openclaw_bridge.consume_openclaw_events_once(db_session, source='default', timeout_seconds=1)

    assert processed == 1
    assert FakeClient.instances == 1
    db_session.expire_all()
    assert db_session.query(OpenClawSyncCursor).filter_by(source='default').one().cursor_value == 'cursor-1'


def test_alembic_upgrade_head_builds_expected_schema(tmp_path):
    db_file = tmp_path / 'alembic_round24.db'
    env = os.environ.copy()
    env.update({
        'APP_ENV': 'development',
        'DATABASE_URL': f'sqlite:///{db_file}',
        'ALLOW_DEV_AUTH': 'false',
        'PYTHONPATH': str(ROOT.parent),
    })

    if not shutil.which('alembic') and importlib.util.find_spec('alembic.config') is None:
        pytest.skip('Alembic CLI/module is not available in this environment')

    command_candidates = []
    if shutil.which('alembic'):
        command_candidates.append(['alembic'])
    command_candidates.append([sys.executable, '-m', 'alembic.config'])
    command_candidates.append([sys.executable, '-c', 'from alembic.config import main; main()'])

    result = None
    for alembic_cmd in command_candidates:
        result = subprocess.run(
            [*alembic_cmd, '-c', str(ROOT / 'alembic.ini'), 'upgrade', 'head'],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            break
    assert result is not None
    assert result.returncode == 0, result.stderr

    engine = create_engine(f'sqlite:///{db_file}', future=True)
    insp = inspect(engine)
    assert 'ticket_ai_intakes' in insp.get_table_names()
    assert 'market_bulletins' in insp.get_table_names()
    assert 'openclaw_attachment_references' in insp.get_table_names()
    assert 'error_code' in {col['name'] for col in insp.get_columns('integration_request_logs')}
    baseline_text = (ROOT / 'alembic' / 'versions' / '20260410_0001_baseline.py').read_text()
    assert 'from app.enums' not in baseline_text
    engine.dispose()


def test_build_source_release_creates_clean_reproducible_package(tmp_path):
    out = tmp_path / 'release.zip'
    result = subprocess.run(
        ['bash', str(ROOT / 'scripts' / 'build_source_release.sh'), str(out)],
        cwd=str(ROOT.parent),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert out.exists()

    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())

    assert 'helpdesk_suite_lite/backend/helpdesk.db' not in names
    assert 'helpdesk_suite_lite/backend/requirements.txt' in names
    assert 'helpdesk_suite_lite/deploy/docker-compose.cloud.yml' in names
    assert 'helpdesk_suite_lite/webapp/package-lock.json' in names
    assert 'helpdesk_suite_lite/Dockerfile' in names
    assert all('__pycache__/' not in name for name in names)
    assert all(not name.endswith('.pyc') for name in names)


def test_requirements_include_prometheus_client():
    requirements = (ROOT / 'requirements.txt').read_text()
    assert 'prometheus-client' in requirements


def test_compose_image_tags_are_aligned_to_current_release():
    compose = (ROOT.parent / 'deploy' / 'docker-compose.cloud.yml').read_text()
    assert compose.count('nexusdesk/helpdesk:round20b') == 4 or compose.count('nexusdesk/helpdesk:round27') == 4
    assert 'round26' not in compose


def make_market(db_session, code='CH', name='Switzerland', country_code='CH'):
    market = Market(code=code, name=name, country_code=country_code)
    db_session.add(market)
    db_session.flush()
    return market


def test_add_ai_intake_inherits_ticket_market_context_when_payload_omits_fields(db_session):
    market = make_market(db_session)
    team = make_team(db_session)
    lead = make_user(db_session, 'lead11', UserRole.lead, team)
    ticket = create_ticket(
        db_session,
        TicketCreate(
            title='Parcel delayed',
            description='Customer reports a delay',
            source=TicketSource.manual,
            source_channel=SourceChannel.whatsapp,
            priority=TicketPriority.medium,
            team_id=team.id,
            market_id=market.id,
            country_code='CH',
        ),
        lead,
    )
    db_session.commit()

    ai = add_ai_intake(
        db_session,
        ticket.id,
        AIIntakeCreate(summary='Customer asks for ETA', classification='delay', confidence=0.92),
        lead,
    )
    db_session.commit()

    assert ai.market_id == market.id
    assert ai.country_code == 'CH'


def test_lite_ai_intake_save_updates_case_without_500(db_session):
    team = make_team(db_session)
    lead = make_user(db_session, 'lead12', UserRole.lead, team)
    ticket = make_ticket(db_session, lead, team=team, contact='+15550009')

    case = save_ai_intake_lite(
        db_session,
        ticket.id,
        LiteAIIntakeRequest(
            ai_summary='Need customs paperwork',
            case_type='customs',
            suggested_required_action='Collect invoice',
            missing_fields='invoice,hs code',
            last_customer_message='Please help',
        ),
        lead,
    )
    db_session.commit()

    refreshed = db_session.query(Ticket).filter(Ticket.id == ticket.id).one()
    assert case.id == ticket.id
    assert refreshed.ai_summary == 'Need customs paperwork'
    assert refreshed.ai_intakes[-1].summary == 'Need customs paperwork'


def test_security_headers_drop_inline_scripts_and_deny_framing():
    from app import main as app_main

    client = TestClient(app_main.app)
    response = client.get('/healthz')

    csp = response.headers['content-security-policy']
    assert "script-src 'self'" in csp
    assert "script-src 'self' 'unsafe-inline'" not in csp
    assert "frame-ancestors 'none'" in csp
    assert response.headers['x-frame-options'] == 'DENY'
    assert 'camera=()' in response.headers['permissions-policy']


def test_source_release_script_defaults_to_current_release_and_includes_report():
    script = (ROOT / 'scripts' / 'build_source_release.sh').read_text()
    assert 'helpdesk_suite_lite_round20B_source_release.zip' in script or 'helpdesk_suite_lite_round27_source_release.zip' in script
    assert 'ROUND20B_LEGACY_PRODUCTION_REPORT.md' in script or 'ROUND27_FRONTEND_OPERATOR_HARDENING_REPORT.md' in script


def test_api_model_uses_field_serializer_not_json_encoders():
    schemas = (ROOT / 'app' / 'schemas.py').read_text()
    assert 'json_encoders' not in schemas
    assert 'field_serializer' in schemas


def test_dockerfile_uses_non_root_runtime_and_healthcheck():
    dockerfile = (ROOT.parent / 'Dockerfile').read_text()
    assert 'USER appuser' in dockerfile
    assert 'HEALTHCHECK' in dockerfile
    assert 'build-essential' not in dockerfile


def test_openclaw_mcp_timeout_includes_recent_stderr():
    from app.services.openclaw_mcp_client import OpenClawMCPClient, OpenClawMCPError

    class FakeStdin:
        def write(self, data):
            return None

        def flush(self):
            return None

    class FakeProcess:
        def __init__(self):
            self.stdin = FakeStdin()

        def poll(self):
            return 9

    client = OpenClawMCPClient()
    client.process = FakeProcess()
    client._stderr_tail.append('pairing required')

    with pytest.raises(OpenClawMCPError) as exc:
        client._request('initialize')

    assert 'pairing required' in str(exc.value)
