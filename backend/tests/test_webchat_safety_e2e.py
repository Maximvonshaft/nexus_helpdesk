import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault('APP_ENV', 'development')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexusdesk_webchat_safety_e2e.db')
os.environ.setdefault('ALLOW_DEV_AUTH', 'false')
os.environ.setdefault('WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT', 'false')
os.environ.setdefault('WEBCHAT_RATE_LIMIT_BACKEND', 'memory')
os.environ.setdefault('WEBCHAT_AI_AUTO_REPLY_MODE', 'safe_ack')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import hash_password  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import MessageStatus, UserRole  # noqa: E402
from app.models import Team, Ticket, TicketComment, TicketOutboundMessage, User  # noqa: E402
from app.webchat_models import WebchatMessage  # noqa: E402
from app.api.webchat import WebchatInitRequest, WebchatSendRequest, WebchatReplyRequest, init_webchat, send_webchat_message, poll_webchat_messages, reply_webchat  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / 'webchat_safety.db'
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
    headers.append((b'user-agent', b'pytest-webchat-safety'))
    return Request({'type': 'http', 'method': 'POST', 'path': path, 'headers': headers})


def make_admin(db_session):
    team = Team(name='Support', team_type='support')
    db_session.add(team)
    db_session.flush()
    user = User(
        username='admin-safety',
        display_name='Admin Safety',
        email='admin-safety@example.com',
        password_hash=hash_password('pass123'),
        role=UserRole.admin,
        team_id=team.id,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def setup_thread(db_session):
    admin = make_admin(db_session)
    init_result = init_webchat(
        WebchatInitRequest(tenant_key='default', channel_key='website', visitor_name='Safety Visitor', origin='http://localhost'),
        make_request(),
        Response(),
        db_session,
    )
    send_webchat_message(
        init_result['conversation_id'],
        WebchatSendRequest(body='Hello, I need support.'),
        make_request(path='/api/webchat/conversations/messages'),
        Response(),
        db_session,
        x_webchat_visitor_token=init_result['visitor_token'],
    )
    ticket = db_session.query(Ticket).one()
    return admin, ticket, init_result


def count_customer_visible_writes(db_session):
    return {
        'agent_messages': db_session.query(WebchatMessage).filter(WebchatMessage.direction == 'agent').count(),
        'sent_outbound': db_session.query(TicketOutboundMessage).filter(TicketOutboundMessage.status == MessageStatus.sent).count(),
        'comments': db_session.query(TicketComment).count(),
    }


def test_webchat_admin_reply_blocks_internal_leak_without_customer_visible_write(db_session):
    admin, ticket, _ = setup_thread(db_session)
    before = count_customer_visible_writes(db_session)

    with pytest.raises(HTTPException) as exc:
        reply_webchat(
            ticket.id,
            WebchatReplyRequest(body='OpenClaw MCP tool call failed with bearer token and database_url'),
            db_session,
            admin,
        )

    assert exc.value.status_code == 400
    assert 'Outbound reply blocked by safety gate' in str(exc.value.detail)
    after = count_customer_visible_writes(db_session)
    assert after == before


def test_webchat_admin_reply_review_required_for_logistics_claim_without_confirm(db_session):
    admin, ticket, _ = setup_thread(db_session)
    before = count_customer_visible_writes(db_session)

    with pytest.raises(HTTPException) as exc:
        reply_webchat(
            ticket.id,
            WebchatReplyRequest(body='Your parcel will arrive tomorrow.', has_fact_evidence=False, confirm_review=False),
            db_session,
            admin,
        )

    assert exc.value.status_code == 409
    assert 'requires human review' in str(exc.value.detail).lower()
    after = count_customer_visible_writes(db_session)
    assert after == before


def test_webchat_admin_reply_confirm_review_allows_reviewed_message(db_session):
    admin, ticket, init_result = setup_thread(db_session)
    body = 'Your parcel will arrive tomorrow.'

    result = reply_webchat(
        ticket.id,
        WebchatReplyRequest(body=body, has_fact_evidence=True, confirm_review=True),
        db_session,
        admin,
    )

    assert result['ok'] is True
    assert result['safety']['level'] in {'allow', 'review'}
    assert db_session.query(WebchatMessage).filter(WebchatMessage.direction == 'agent', WebchatMessage.body == body).count() == 1
    assert db_session.query(TicketOutboundMessage).filter(TicketOutboundMessage.status == MessageStatus.sent, TicketOutboundMessage.body == body).count() == 1

    polled = poll_webchat_messages(
        init_result['conversation_id'],
        make_request(path='/api/webchat/conversations/messages'),
        Response(),
        x_webchat_visitor_token=init_result['visitor_token'],
        db=db_session,
    )
    assert any(item['direction'] == 'agent' and item['body'] == body for item in polled['messages'])
