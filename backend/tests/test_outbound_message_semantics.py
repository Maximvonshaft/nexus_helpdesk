import os
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault('APP_ENV', 'development')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/helpdesk_suite_outbound_semantics.db')
os.environ.setdefault('ALLOW_DEV_AUTH', 'false')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import hash_password  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import MessageStatus, ResolutionCategory, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.models import Team, Ticket, TicketOutboundMessage, User  # noqa: E402
from app.services.message_dispatch import claim_pending_messages, process_outbound_message, requeue_dead_outbound_message  # noqa: E402
from app.services.outbound_semantics import count_outbound_semantics, outbound_ui_label  # noqa: E402
from app.services.timeline_service import serialize_outbound  # noqa: E402
from app.services import openclaw_bridge as openclaw_bridge_service  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / 'outbound_semantics.db'
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


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def make_team(db_session):
    team = Team(name=f'Ops-{_uid()}', team_type='support')
    db_session.add(team)
    db_session.flush()
    return team


def make_user(db_session, team=None, role=UserRole.lead):
    suffix = _uid()
    user = User(
        username=f'ops-user-{suffix}',
        display_name='Ops User',
        email=f'ops-user-{suffix}@example.com',
        password_hash=hash_password('pass123'),
        role=role,
        team_id=team.id if team else None,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def make_ticket(db_session, *, channel=SourceChannel.whatsapp, contact='+15550001'):
    team = make_team(db_session)
    make_user(db_session, team)
    ticket = Ticket(
        ticket_no=f'T-{channel.value}-{_uid()}',
        title='Customer message',
        description='Customer message',
        source=TicketSource.user_message,
        source_channel=channel,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        resolution_category=ResolutionCategory.none,
        team_id=team.id,
        source_chat_id=contact,
        preferred_reply_channel=channel.value,
        preferred_reply_contact=contact,
    )
    db_session.add(ticket)
    db_session.flush()
    return ticket


def add_outbound(db_session, ticket, *, channel, status, provider_status, body='hello'):
    row = TicketOutboundMessage(ticket_id=ticket.id, channel=channel, status=status, body=body, provider_status=provider_status, max_retries=3)
    db_session.add(row)
    db_session.flush()
    return row


def test_webchat_safe_ack_and_fallback_do_not_count_as_external_pending(db_session):
    ticket = make_ticket(db_session, channel=SourceChannel.web_chat, contact='wc-local')
    add_outbound(db_session, ticket, channel=SourceChannel.web_chat, status=MessageStatus.sent, provider_status='webchat_safe_ack_delivered')
    add_outbound(db_session, ticket, channel=SourceChannel.web_chat, status=MessageStatus.sent, provider_status='webchat_ai_safe_fallback')
    add_outbound(db_session, ticket, channel=SourceChannel.web_chat, status=MessageStatus.pending, provider_status='queued')
    counts = count_outbound_semantics(db_session)
    assert counts['external_pending_outbound'] == 0
    assert counts['external_dead_outbound'] == 0
    assert counts['webchat_local_ack_sent'] == 1
    assert counts['webchat_ai_safe_fallback_sent'] == 1


def test_only_email_and_whatsapp_pending_count_as_external_pending(db_session):
    whatsapp_ticket = make_ticket(db_session, channel=SourceChannel.whatsapp, contact='+15550101')
    email_ticket = make_ticket(db_session, channel=SourceChannel.email, contact='customer@example.com')
    telegram_ticket = make_ticket(db_session, channel=SourceChannel.telegram, contact='telegram:42')
    sms_ticket = make_ticket(db_session, channel=SourceChannel.sms, contact='+15550102')
    add_outbound(db_session, whatsapp_ticket, channel=SourceChannel.whatsapp, status=MessageStatus.pending, provider_status='queued')
    add_outbound(db_session, email_ticket, channel=SourceChannel.email, status=MessageStatus.dead, provider_status='dead:max_retries')
    add_outbound(db_session, telegram_ticket, channel=SourceChannel.telegram, status=MessageStatus.pending, provider_status='queued')
    add_outbound(db_session, sms_ticket, channel=SourceChannel.sms, status=MessageStatus.dead, provider_status='dead:max_retries')
    counts = count_outbound_semantics(db_session)
    assert counts['external_pending_outbound'] == 1
    assert counts['external_dead_outbound'] == 1


def test_outbound_ui_labels_are_semantic_not_raw_provider_statuses(db_session):
    ticket = make_ticket(db_session, channel=SourceChannel.web_chat, contact='wc-label')
    local_ack = add_outbound(db_session, ticket, channel=SourceChannel.web_chat, status=MessageStatus.sent, provider_status='webchat_safe_ack_delivered')
    safe_fallback = add_outbound(db_session, ticket, channel=SourceChannel.web_chat, status=MessageStatus.sent, provider_status='webchat_ai_safe_fallback')
    draft = add_outbound(db_session, ticket, channel=SourceChannel.whatsapp, status=MessageStatus.draft, provider_status='ai_review_required')
    pending = add_outbound(db_session, ticket, channel=SourceChannel.whatsapp, status=MessageStatus.pending, provider_status='queued')
    assert outbound_ui_label(local_ack.channel, local_ack.status, local_ack.provider_status) == 'Local WebChat ACK'
    assert outbound_ui_label(safe_fallback.channel, safe_fallback.status, safe_fallback.provider_status) == 'WebChat Safe Fallback'
    assert outbound_ui_label(draft.channel, draft.status, draft.provider_status) == 'Draft / Review Required'
    assert outbound_ui_label(pending.channel, pending.status, pending.provider_status) == 'External Send Pending'
    assert serialize_outbound(local_ack)['payload']['is_external_send'] is False
    assert serialize_outbound(pending)['payload']['is_external_send'] is True


def test_claim_pending_messages_ignores_local_webchat_pending_rows(db_session):
    webchat_ticket = make_ticket(db_session, channel=SourceChannel.web_chat, contact='wc-pending')
    whatsapp_ticket = make_ticket(db_session, channel=SourceChannel.whatsapp, contact='+15550103')
    webchat_row = add_outbound(db_session, webchat_ticket, channel=SourceChannel.web_chat, status=MessageStatus.pending, provider_status='queued')
    whatsapp_row = add_outbound(db_session, whatsapp_ticket, channel=SourceChannel.whatsapp, status=MessageStatus.pending, provider_status='queued')
    db_session.commit()
    claimed = claim_pending_messages(db_session, worker_id='worker-test')
    assert [row.id for row in claimed] == [whatsapp_row.id]
    db_session.refresh(webchat_row)
    db_session.refresh(whatsapp_row)
    assert webchat_row.status == MessageStatus.pending
    assert whatsapp_row.status == MessageStatus.processing


def test_non_customer_outbound_never_calls_provider_dispatch(db_session, monkeypatch):
    ticket = make_ticket(db_session, channel=SourceChannel.web_chat, contact='wc-send-block')
    row = add_outbound(db_session, ticket, channel=SourceChannel.web_chat, status=MessageStatus.pending, provider_status='queued')
    db_session.commit()
    monkeypatch.setattr('app.services.message_dispatch.dispatch_via_openclaw_bridge', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('provider path must not run for web_chat')))
    monkeypatch.setattr('app.services.message_dispatch.dispatch_via_openclaw_mcp', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('mcp path must not run for web_chat')))
    monkeypatch.setattr('app.services.message_dispatch.dispatch_via_openclaw_cli', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('cli path must not run for web_chat')))
    processed = process_outbound_message(db_session, row)
    assert processed.status == MessageStatus.dead
    assert processed.failure_code == 'customer_outbound_channel_not_allowed'


def test_requeue_dead_outbound_rejects_local_webchat_ack(db_session):
    ticket = make_ticket(db_session, channel=SourceChannel.web_chat, contact='wc-dead')
    row = add_outbound(db_session, ticket, channel=SourceChannel.web_chat, status=MessageStatus.dead, provider_status='webchat_safe_ack_delivered')
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        requeue_dead_outbound_message(db_session, message_id=row.id)
    assert exc.value.status_code == 400
    assert 'external outbound' in exc.value.detail


def test_inbound_auto_sync_does_not_create_outbound_messages(db_session, monkeypatch):
    team = make_team(db_session)
    make_user(db_session, team)
    monkeypatch.setattr(openclaw_bridge_service.settings, 'openclaw_sync_enabled', True)
    monkeypatch.setattr(openclaw_bridge_service.settings, 'openclaw_inbound_auto_sync_enabled', True)
    monkeypatch.setattr(openclaw_bridge_service.settings, 'openclaw_inbound_auto_sync_interval_seconds', 0)
    monkeypatch.setattr(openclaw_bridge_service.settings, 'openclaw_inbound_sync_limit', 10)
    monkeypatch.setattr(openclaw_bridge_service.settings, 'openclaw_inbound_sync_message_limit', 20)
    monkeypatch.setattr(openclaw_bridge_service.settings, 'openclaw_inbound_sync_include_groups', False)
    monkeypatch.setattr(openclaw_bridge_service.settings, 'openclaw_bridge_enabled', True)
    monkeypatch.setattr(openclaw_bridge_service, 'dispatch_via_openclaw_bridge', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('provider dispatch should not run')))
    monkeypatch.setattr(openclaw_bridge_service, 'dispatch_via_openclaw_mcp', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('mcp dispatch should not run')))
    monkeypatch.setattr(openclaw_bridge_service, 'dispatch_via_openclaw_cli', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('cli dispatch should not run')))
    monkeypatch.setattr(openclaw_bridge_service, 'list_openclaw_conversations', lambda **kwargs: {'conversations': [{'sessionKey': 'sess-inbound-no-outbox', 'route': {'channel': 'whatsapp', 'recipient': '+15550104', 'accountId': 'default'}}]})
    monkeypatch.setattr(openclaw_bridge_service, 'read_openclaw_bridge_conversation', lambda session_key, limit=50: ({'sessionKey': session_key, 'route': {'channel': 'whatsapp', 'recipient': '+15550104', 'accountId': 'default'}}, [{'id': 'msg-inbound-1', 'role': 'user', 'author': 'customer', 'text': 'hello inbound'}]))
    monkeypatch.setattr(openclaw_bridge_service, 'fetch_openclaw_bridge_attachments', lambda *args, **kwargs: [])
    summary = openclaw_bridge_service.sync_openclaw_inbound_conversations_once(db_session, source='default', force=True)
    db_session.commit()
    assert summary['synced_conversations'] == 1
    assert db_session.query(TicketOutboundMessage).count() == 0


def test_worker_disabled_outbound_still_never_claims_or_dispatches(monkeypatch):
    from scripts import run_worker
    @contextmanager
    def dummy_db_context():
        yield SimpleNamespace()
    monkeypatch.setattr(run_worker.settings, 'enable_outbound_dispatch', False)
    monkeypatch.setattr(run_worker.settings, 'openclaw_sync_enabled', False)
    monkeypatch.setattr(run_worker.settings, 'openclaw_inbound_auto_sync_enabled', False)
    monkeypatch.setattr(run_worker, 'db_context', dummy_db_context)
    monkeypatch.setattr(run_worker, 'dispatch_pending_messages', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('outbound dispatch should not run')))
    monkeypatch.setattr(run_worker, 'dispatch_pending_background_jobs', lambda *args, **kwargs: [])
    monkeypatch.setattr(run_worker, 'record_queue_snapshot', lambda *args, **kwargs: None)
    monkeypatch.setattr(run_worker, 'record_worker_poll', lambda *args, **kwargs: None)
    monkeypatch.setattr(run_worker, 'record_worker_result', lambda *args, **kwargs: None)
    monkeypatch.setattr(run_worker, 'log_event', lambda *args, **kwargs: None)
    assert run_worker.run_once('worker-test') == 0
