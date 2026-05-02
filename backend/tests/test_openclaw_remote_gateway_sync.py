import json
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault('APP_ENV', 'development')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/helpdesk_suite_remote_gateway_sync.db')
os.environ.setdefault('ALLOW_DEV_AUTH', 'false')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.enums import SourceChannel, TicketPriority, TicketSource  # noqa: E402
from app.models import BackgroundJob, OpenClawConversationLink, Ticket  # noqa: E402
from app.services import background_jobs, openclaw_client_factory, openclaw_bridge  # noqa: E402
from app.services.background_jobs import OPENCLAW_SYNC_JOB  # noqa: E402
from app.utils.time import utc_now  # noqa: E402
from backend.scripts import sync_openclaw_sessions  # noqa: E402


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


def make_ticket(db_session, contact='+41798559737'):
    row = Ticket(
        ticket_no=f'T-{utc_now().timestamp():.0f}',
        title='Remote sync ticket',
        description='Customer message',
        source=TicketSource.user_message,
        source_channel=SourceChannel.whatsapp,
        priority=TicketPriority.medium,
        source_chat_id=contact,
        preferred_reply_contact=contact,
        preferred_reply_channel='whatsapp',
    )
    db_session.add(row)
    db_session.flush()
    return row


class DummyRemoteClient:
    def __init__(self, payload):
        self.payload = payload
        self.entered = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def conversations_list(self, *, limit=50, agent='support'):
        return self.payload


def test_remote_bootstrap_uses_runtime_client_and_creates_link(db_session, monkeypatch, capsys):
    ticket = make_ticket(db_session)
    payload = {
        'conversations': [
            {
                'sessionKey': 'agent:support:whatsapp:direct:+41798559737',
                'recipient': '+41798559737',
                'channel': 'whatsapp',
                'route': {'channel': 'whatsapp', 'recipient': '+41798559737'},
            }
        ]
    }
    monkeypatch.setattr(sync_openclaw_sessions, 'SessionLocal', lambda: db_session)
    monkeypatch.setattr(sync_openclaw_sessions, 'get_openclaw_runtime_client', lambda: DummyRemoteClient(payload))
    monkeypatch.setattr(sync_openclaw_sessions, 'sync_openclaw_conversation', lambda *args, **kwargs: None)
    monkeypatch.setattr(sys, 'argv', ['sync_openclaw_sessions.py', '--limit', '100'])

    assert sync_openclaw_sessions.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out['ok'] is True
    assert out['synced'] == 1
    assert db_session.query(OpenClawConversationLink).filter_by(ticket_id=ticket.id).count() == 1


def test_remote_bootstrap_unmatched_is_ok(db_session, monkeypatch, capsys):
    payload = {'conversations': [{'sessionKey': 'sess-unmatched', 'recipient': '+41000000000'}]}
    monkeypatch.setattr(sync_openclaw_sessions, 'SessionLocal', lambda: db_session)
    monkeypatch.setattr(sync_openclaw_sessions, 'get_openclaw_runtime_client', lambda: DummyRemoteClient(payload))
    monkeypatch.setattr(sys, 'argv', ['sync_openclaw_sessions.py'])

    assert sync_openclaw_sessions.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out['ok'] is True
    assert out['synced'] == 0
    assert out['unmatched'] == 1
    assert out['reason'] == 'no_matching_ticket'


def test_remote_bridge_client_conversations_list_posts_to_expected_endpoint(monkeypatch):
    captured = {}

    class DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def read(self):
            return b'{"ok": true, "conversations": []}'

    def fake_urlopen(request, timeout):
        captured['url'] = request.full_url
        captured['body'] = json.loads(request.data.decode('utf-8'))
        return DummyResponse()

    monkeypatch.setattr(openclaw_client_factory.urllib.request, 'urlopen', fake_urlopen)
    client = openclaw_client_factory.OpenClawBridgeHTTPClient(bridge_url='http://bridge.local:18792', timeout_seconds=3)
    result = client.conversations_list(limit=123, agent='support')

    assert captured['url'] == 'http://bridge.local:18792/conversations-list'
    assert captured['body'] == {'limit': 123, 'agent': 'support'}
    assert result == {'ok': True, 'conversations': []}


def test_background_sync_job_uses_runtime_client_factory(db_session, monkeypatch):
    ticket = make_ticket(db_session)
    job = BackgroundJob(
        queue_name='openclaw_sync',
        job_type=OPENCLAW_SYNC_JOB,
        payload_json=json.dumps({'ticket_id': ticket.id, 'session_key': 'sess-1', 'transcript_limit': 5}),
    )
    db_session.add(job)
    db_session.flush()
    calls = []

    class DummyClient:
        def __enter__(self):
            calls.append('entered')
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(background_jobs.settings, 'openclaw_sync_enabled', True)
    monkeypatch.setattr(background_jobs.openclaw_client_factory, 'get_openclaw_runtime_client', lambda: DummyClient())
    monkeypatch.setattr(background_jobs.openclaw_bridge, 'sync_openclaw_conversation', lambda *args, **kwargs: calls.append(kwargs.get('client')))

    background_jobs.process_background_job(db_session, job)
    assert job.status.value == 'done'
    assert calls[0] == 'entered'
    assert calls[1].__class__.__name__ == 'DummyClient'


def test_bridge_server_has_read_only_conversations_endpoint():
    source = (ROOT.parent / 'backend' / 'scripts' / 'openclaw_bridge_server.js').read_text(encoding='utf-8')
    assert '/conversations-list' in source
    block = source.split('async listConversations(payload)', 1)[1].split('async getConversation(payload)', 1)[0]
    assert 'sessions.list' in block
    assert "this.client.request('send'" not in block
    assert "sessions' + '.' + 'send" not in block


def test_run_sync_daemon_once_script_has_once_flag():
    source = (ROOT.parent / 'backend' / 'scripts' / 'run_openclaw_sync_daemon.py').read_text(encoding='utf-8')
    assert '--once' in source
