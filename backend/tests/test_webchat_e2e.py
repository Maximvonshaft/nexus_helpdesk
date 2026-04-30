import os
import sys
from pathlib import Path

import pytest
from fastapi import Response
from starlette.requests import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault('APP_ENV', 'development')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexusdesk_webchat_e2e.db')
os.environ.setdefault('ALLOW_DEV_AUTH', 'false')
os.environ.setdefault('WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT', 'false')
os.environ.setdefault('WEBCHAT_RATE_LIMIT_BACKEND', 'memory')
os.environ.setdefault('WEBCHAT_AI_AUTO_REPLY_MODE', 'safe_ack')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import hash_password  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import JobStatus, MessageStatus, NoteVisibility, SourceChannel, TicketStatus, UserRole  # noqa: E402
from app.models import BackgroundJob, Customer, Team, Ticket, TicketComment, TicketEvent, TicketOutboundMessage, User  # noqa: E402
from app.webchat_models import WebchatConversation, WebchatMessage  # noqa: E402
from app.api.webchat import WebchatInitRequest, WebchatSendRequest, WebchatReplyRequest, init_webchat, send_webchat_message, poll_webchat_messages, get_webchat_thread, reply_webchat  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / 'webchat_e2e.db'
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


def make_request(origin='http://localhost', path='/api/webchat/init'):
    headers = []
    if origin is not None:
        headers.append((b'origin', origin.encode('utf-8')))
    headers.append((b'user-agent', b'pytest-webchat'))
    return Request({'type': 'http', 'method': 'POST', 'path': path, 'headers': headers})


def make_admin(db_session):
    team = Team(name='Support', team_type='support')
    db_session.add(team)
    db_session.flush()
    user = User(
        username='admin-webchat',
        display_name='Admin Webchat',
        email='admin-webchat@example.com',
        password_hash=hash_password('pass123'),
        role=UserRole.admin,
        team_id=team.id,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def create_conversation(db_session):
    payload = WebchatInitRequest(
        tenant_key='default',
        channel_key='website',
        visitor_name='Visitor One',
        visitor_email='visitor@example.com',
        origin='http://localhost',
        page_url='http://localhost/support',
    )
    result = init_webchat(payload, make_request(), Response(), db_session)
    return result


def test_webchat_init_creates_customer_ticket_conversation_event(db_session):
    result = create_conversation(db_session)

    assert result['conversation_id']
    assert result['visitor_token']
    assert db_session.query(Customer).count() == 1
    assert db_session.query(Ticket).count() == 1
    assert db_session.query(WebchatConversation).count() == 1

    conversation = db_session.query(WebchatConversation).one()
    ticket = db_session.query(Ticket).one()
    assert conversation.ticket_id == ticket.id
    assert ticket.source_channel == SourceChannel.web_chat
    assert ticket.preferred_reply_channel == SourceChannel.web_chat.value
    assert db_session.query(TicketEvent).filter(TicketEvent.ticket_id == ticket.id).count() >= 1


def test_webchat_visitor_message_updates_ticket_and_is_pollable(db_session):
    result = create_conversation(db_session)
    conversation_id = result['conversation_id']
    visitor_token = result['visitor_token']
    body = 'Hello, I need help with my parcel TRK123456.'

    sent = send_webchat_message(
        conversation_id,
        WebchatSendRequest(body=body),
        make_request(path=f'/api/webchat/conversations/{conversation_id}/messages'),
        Response(),
        db_session,
        x_webchat_visitor_token=visitor_token,
    )

    assert sent['ok'] is True
    visitor_msg = db_session.query(WebchatMessage).filter(WebchatMessage.direction == 'visitor').one()
    assert visitor_msg.body == body

    ticket = db_session.query(Ticket).one()
    assert ticket.last_customer_message == body
    assert ticket.customer_request == body
    assert db_session.query(TicketComment).filter(TicketComment.ticket_id == ticket.id, TicketComment.visibility == NoteVisibility.external).count() >= 1
    assert db_session.query(BackgroundJob).filter(BackgroundJob.job_type == 'webchat.ai_reply', BackgroundJob.status == JobStatus.pending).count() == 1

    polled = poll_webchat_messages(
        conversation_id,
        make_request(path=f'/api/webchat/conversations/{conversation_id}/messages'),
        Response(),
        x_webchat_visitor_token=visitor_token,
        db=db_session,
    )
    assert any(item['direction'] == 'visitor' and item['body'] == body for item in polled['messages'])


def test_webchat_admin_reply_is_visible_to_visitor_polling(db_session):
    admin = make_admin(db_session)
    result = create_conversation(db_session)
    conversation_id = result['conversation_id']
    visitor_token = result['visitor_token']

    send_webchat_message(
        conversation_id,
        WebchatSendRequest(body='Where is my parcel TRK123456?'),
        make_request(path=f'/api/webchat/conversations/{conversation_id}/messages'),
        Response(),
        db_session,
        x_webchat_visitor_token=visitor_token,
    )
    ticket = db_session.query(Ticket).one()
    reply_body = 'We have received your request and will check it shortly.'

    thread_before = get_webchat_thread(ticket.id, db_session, admin)
    assert thread_before['ticket_id'] == ticket.id

    result = reply_webchat(
        ticket.id,
        WebchatReplyRequest(body=reply_body, has_fact_evidence=False, confirm_review=False),
        db_session,
        admin,
    )
    assert result['ok'] is True
    assert db_session.query(WebchatMessage).filter(WebchatMessage.direction == 'agent', WebchatMessage.body == reply_body).count() == 1
    assert db_session.query(TicketOutboundMessage).filter(TicketOutboundMessage.status == MessageStatus.sent).count() >= 1

    db_session.refresh(ticket)
    assert ticket.status == TicketStatus.waiting_customer

    polled = poll_webchat_messages(
        conversation_id,
        make_request(path=f'/api/webchat/conversations/{conversation_id}/messages'),
        Response(),
        x_webchat_visitor_token=visitor_token,
        db=db_session,
    )
    assert any(item['direction'] == 'agent' and item['body'] == reply_body for item in polled['messages'])
