from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
DEPS = PROJECT / '.pydeps'

if str(DEPS) not in sys.path:
    sys.path.insert(0, str(DEPS))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TMPDIR = tempfile.TemporaryDirectory(prefix='nexus-governance-round4-')
DB_PATH = Path(TMPDIR.name) / 'smoke.db'
os.environ['DATABASE_URL'] = f'sqlite:///{DB_PATH}'
os.environ['APP_ENV'] = 'development'
os.environ['OPENCLAW_BRIDGE_ENABLED'] = 'false'
os.environ['OPENCLAW_CLI_FALLBACK_ENABLED'] = 'false'

from fastapi import HTTPException
from pydantic import ValidationError

from app.auth_service import verify_password
from app.db import Base, SessionLocal, engine
from app.enums import SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole
from app.models import ChannelAccount, Market, OpenClawConversationLink, OpenClawUnresolvedEvent, Team, Ticket, User
from app.schemas import ChannelAccountCreate, ChannelAccountUpdate, PasswordResetRequest, UserCreate, UserUpdate
from app.api.admin import (
    activate_user,
    create_channel_account,
    create_user,
    deactivate_user,
    drop_unresolved_event,
    list_unresolved_events,
    replay_unresolved_event,
    reset_user_password,
    update_channel_account,
    update_user,
)
from app.services import message_dispatch
from app.services.message_dispatch import process_outbound_message, queue_outbound_message
from app.services.openclaw_bridge import persist_unresolved_openclaw_event, process_openclaw_inbound_event


Base.metadata.create_all(bind=engine)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def expect_http_error(fn, expected_substring: str) -> None:
    try:
        fn()
    except HTTPException as exc:
        detail = str(exc.detail)
        check(expected_substring in detail, f'Expected error containing {expected_substring!r}, got {detail!r}')
        return
    except ValidationError as exc:
        detail = str(exc)
        check(expected_substring in detail, f'Expected validation containing {expected_substring!r}, got {detail!r}')
        return
    raise AssertionError(f'Expected HTTPException containing {expected_substring!r}')


def create_admin(db):
    admin = User(
        username='admin',
        display_name='Admin',
        email='admin@example.com',
        password_hash='seed',
        role=UserRole.admin,
        is_active=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


def create_market_and_team(db, code: str, name: str):
    market = Market(code=code, name=name, country_code=code, is_active=True)
    db.add(market)
    db.flush()
    team = Team(name=f'{name} Support', team_type='support', market_id=market.id, is_active=True)
    db.add(team)
    db.commit()
    db.refresh(market)
    db.refresh(team)
    return market, team


def create_ticket(db, *, ticket_no: str, contact: str, market_id: int | None, team_id: int | None = None):
    ticket = Ticket(
        ticket_no=ticket_no,
        title=f'Ticket {ticket_no}',
        description='Smoke',
        source=TicketSource.manual,
        source_channel=SourceChannel.whatsapp,
        priority=TicketPriority.medium,
        status=TicketStatus.new,
        market_id=market_id,
        team_id=team_id,
        source_chat_id=contact,
        preferred_reply_contact=contact,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


def run_user_lifecycle_smoke(db, admin):
    created = create_user(
        UserCreate(username='  alice  ', password='secret1', display_name='  Alice  ', email='  ALICE@EXAMPLE.COM ', role=UserRole.agent, capabilities=[]),
        db,
        admin,
    )
    check(created.username == 'alice', 'username.trim() failed on create')
    check(created.display_name == 'Alice', 'display_name.trim() failed on create')
    check(created.email == 'alice@example.com', 'email normalization failed on create')

    expect_http_error(
        lambda: create_user(UserCreate(username='alice', password='secret1', display_name='Dup', email='dup@example.com', role=UserRole.agent, capabilities=[]), db, admin),
        'Username already exists',
    )
    expect_http_error(
        lambda: create_user(UserCreate(username='bob', password='123', display_name='Bob', email='bob@example.com', role=UserRole.agent, capabilities=[]), db, admin),
        'at least 6 characters',
    )

    second = create_user(
        UserCreate(username='bob', password='secret2', display_name='Bob', email='bob@example.com', role=UserRole.agent, capabilities=[]),
        db,
        admin,
    )
    expect_http_error(
        lambda: update_user(second.id, UserUpdate(email=' ALICE@example.com '), db, admin),
        'Email already exists',
    )
    updated = update_user(second.id, UserUpdate(display_name='  Bob Two  ', email=' BOB2@example.com '), db, admin)
    check(updated.display_name == 'Bob Two', 'display_name.trim() failed on update')
    check(updated.email == 'bob2@example.com', 'email normalization failed on update')

    deactivated = deactivate_user(second.id, db, admin)
    check(deactivated.is_active is False, 'deactivate_user failed')
    activated = activate_user(second.id, db, admin)
    check(activated.is_active is True, 'activate_user failed')
    reset_user_password(second.id, PasswordResetRequest(password='secret3'), db, admin)
    db.refresh(db.query(User).filter(User.id == second.id).first())
    stored = db.query(User).filter(User.id == second.id).first()
    check(verify_password('secret3', stored.password_hash), 'reset_user_password failed to persist hash')

    print('PASS user lifecycle smoke')
    return created, second


def run_channel_account_smoke(db, admin, market_a, market_b):
    global_wh = create_channel_account(ChannelAccountCreate(provider='whatsapp', account_id='wa-global', display_name='Global WA'), db, admin)
    create_channel_account(ChannelAccountCreate(provider='telegram', account_id='tg-global', display_name='Global TG'), db, admin)
    market_a_wh = create_channel_account(ChannelAccountCreate(provider='whatsapp', account_id='wa-mkt-a', display_name='Market A WA', market_id=market_a.id), db, admin)
    create_channel_account(ChannelAccountCreate(provider='whatsapp', account_id='wa-mkt-b', display_name='Market B WA', market_id=market_b.id), db, admin)

    expect_http_error(lambda: create_channel_account(ChannelAccountCreate(provider='email', account_id='bad-provider'), db, admin), 'Unsupported channel provider')
    expect_http_error(lambda: create_channel_account(ChannelAccountCreate(provider='whatsapp', account_id='wa-self', fallback_account_id='wa-self'), db, admin), 'Fallback cannot point to itself')
    expect_http_error(lambda: create_channel_account(ChannelAccountCreate(provider='whatsapp', account_id='wa-missing', fallback_account_id='wa-not-found'), db, admin), 'Fallback channel account not found')
    expect_http_error(lambda: create_channel_account(ChannelAccountCreate(provider='whatsapp', account_id='wa-provider-mismatch', fallback_account_id='tg-global'), db, admin), 'Fallback provider must match')
    expect_http_error(lambda: create_channel_account(ChannelAccountCreate(provider='whatsapp', account_id='wa-global-bad', fallback_account_id='wa-mkt-a'), db, admin), 'Global primary account cannot fallback')
    expect_http_error(lambda: create_channel_account(ChannelAccountCreate(provider='whatsapp', account_id='wa-market-bad', market_id=market_a.id, fallback_account_id='wa-mkt-b'), db, admin), 'Fallback market must be global or match')

    updated = update_channel_account(market_a_wh.id, ChannelAccountUpdate(fallback_account_id='wa-global'), db, admin)
    check(updated.fallback_account_id == 'wa-global', 'channel account update failed to persist valid fallback')

    print('PASS channel account validation smoke')
    return global_wh, market_a_wh


def run_first_send_route_smoke(db, admin, market_a, team_a, global_wh, market_a_wh):
    captured: list[dict] = []
    original_dispatch = message_dispatch.dispatch_via_openclaw_bridge
    original_cli_fallback = message_dispatch.settings.openclaw_cli_fallback_enabled

    def fake_dispatch(*, channel, target, body, account_id=None, thread_id=None, session_key=None):
        captured.append({'channel': channel, 'target': target, 'account_id': account_id, 'session_key': session_key})
        return message_dispatch.MessageStatus.sent, 'sent', None

    message_dispatch.dispatch_via_openclaw_bridge = fake_dispatch
    message_dispatch.settings.openclaw_cli_fallback_enabled = False
    try:
        explicit_ticket = create_ticket(db, ticket_no='T-ROUTE-1', contact='cust-route-1', market_id=market_a.id, team_id=team_a.id)
        link = OpenClawConversationLink(ticket_id=explicit_ticket.id, session_key='sess-route-explicit', channel='whatsapp', recipient='cust-route-1', account_id=global_wh.account_id)
        db.add(link)
        db.commit()
        db.refresh(explicit_ticket)
        message = queue_outbound_message(db, ticket_id=explicit_ticket.id, channel=SourceChannel.whatsapp, body='hello explicit', created_by=admin.id)
        process_outbound_message(db, message)
        check(captured[-1]['account_id'] == 'wa-global', 'explicit account_id route not honored')

        market_ticket = create_ticket(db, ticket_no='T-ROUTE-2', contact='cust-route-2', market_id=market_a.id, team_id=team_a.id)
        message = queue_outbound_message(db, ticket_id=market_ticket.id, channel=SourceChannel.whatsapp, body='hello market', created_by=admin.id)
        process_outbound_message(db, message)
        check(captured[-1]['account_id'] == 'wa-mkt-a', 'market route not chosen for first send')

        global_ticket = create_ticket(db, ticket_no='T-ROUTE-3', contact='cust-route-3', market_id=None, team_id=team_a.id)
        message = queue_outbound_message(db, ticket_id=global_ticket.id, channel=SourceChannel.whatsapp, body='hello global', created_by=admin.id)
        process_outbound_message(db, message)
        check(captured[-1]['account_id'] == 'wa-global', 'global fallback route not chosen for first send')
    finally:
        message_dispatch.dispatch_via_openclaw_bridge = original_dispatch
        message_dispatch.settings.openclaw_cli_fallback_enabled = original_cli_fallback

    print('PASS first-send route smoke')


def run_unresolved_event_smoke(db, admin, market_a, team_a):
    create_ticket(db, ticket_no='T-UNR-1', contact='cust-amb', market_id=market_a.id, team_id=team_a.id)
    create_ticket(db, ticket_no='T-UNR-2', contact='cust-amb', market_id=market_a.id, team_id=team_a.id)
    ambiguous_event = {
        'type': 'message',
        'sessionKey': 'sess-unresolved-amb',
        'route': {'channel': 'whatsapp', 'recipient': 'cust-amb'},
    }
    processed = process_openclaw_inbound_event(db, event=ambiguous_event, source='smoke')
    check(processed is False, 'ambiguous inbound event should not process immediately')
    row = db.query(OpenClawUnresolvedEvent).filter(OpenClawUnresolvedEvent.session_key == 'sess-unresolved-amb').one()
    check(row.status == 'pending', 'ambiguous event should persist as pending unresolved event')

    failure_ticket = create_ticket(db, ticket_no='T-UNR-3', contact='cust-fail', market_id=market_a.id, team_id=team_a.id)
    failure_row = persist_unresolved_openclaw_event(
        db,
        source='smoke',
        session_key='sess-unresolved-fail',
        event_type='message',
        recipient='cust-fail',
        source_chat_id='cust-fail',
        preferred_reply_contact='cust-fail',
        payload={'type': 'message', 'sessionKey': 'sess-unresolved-fail', 'route': {'channel': 'whatsapp', 'recipient': 'cust-fail'}},
    )
    db.commit()

    import app.services.openclaw_bridge as openclaw_bridge_module
    original_sync = openclaw_bridge_module.sync_openclaw_conversation

    sync_calls: list[str] = []

    def failing_sync(*args, **kwargs):
        raise RuntimeError('forced sync failure')

    openclaw_bridge_module.sync_openclaw_conversation = failing_sync
    replay_result = replay_unresolved_event(failure_row.id, db, admin)
    check(replay_result['ok'] is False, 'failed replay should return ok=false')
    db.refresh(failure_row)
    check(failure_row.status == 'failed', 'failed replay should set failed status')
    check('forced sync failure' in (failure_row.last_error or ''), 'failed replay should persist last_error')

    survivor = db.query(Ticket).filter(Ticket.ticket_no == 'T-UNR-1').one()
    duplicate = db.query(Ticket).filter(Ticket.ticket_no == 'T-UNR-2').one()
    duplicate.status = TicketStatus.closed
    db.commit()

    def successful_sync(*args, **kwargs):
        sync_calls.append(kwargs['session_key'])
        return None

    openclaw_bridge_module.sync_openclaw_conversation = successful_sync
    replay_result = replay_unresolved_event(row.id, db, admin)
    check(replay_result['ok'] is True, 'resolved replay should return ok=true')
    db.refresh(row)
    check(row.status == 'resolved', 'resolved replay should update status')
    check(row.last_error is None, 'successful replay should clear last_error')
    check(sync_calls == ['sess-unresolved-amb'], 'successful replay should re-run sync exactly once')
    check(db.query(OpenClawConversationLink).filter(OpenClawConversationLink.session_key == 'sess-unresolved-amb').count() == 1, 'replay should not create duplicate links')

    replay_unresolved_event(row.id, db, admin)
    check(db.query(OpenClawConversationLink).filter(OpenClawConversationLink.session_key == 'sess-unresolved-amb').count() == 1, 'repeat replay should stay idempotent on link creation')

    dropped_row = persist_unresolved_openclaw_event(
        db,
        source='smoke',
        session_key='sess-unresolved-drop',
        event_type='message',
        recipient='cust-drop',
        source_chat_id='cust-drop',
        preferred_reply_contact='cust-drop',
        payload={'type': 'message', 'sessionKey': 'sess-unresolved-drop', 'route': {'channel': 'whatsapp', 'recipient': 'cust-drop'}},
    )
    db.commit()
    drop_unresolved_event(dropped_row.id, db, admin)
    db.refresh(dropped_row)
    check(dropped_row.status == 'dropped', 'drop unresolved event failed')
    check(len(list_unresolved_events(db, admin)) >= 3, 'list unresolved events should include persisted rows')

    openclaw_bridge_module.sync_openclaw_conversation = original_sync
    print('PASS unresolved event replay smoke')


def main():
    db = SessionLocal()
    try:
        admin = create_admin(db)
        market_a, team_a = create_market_and_team(db, 'NG', 'Nigeria')
        market_b, _team_b = create_market_and_team(db, 'KE', 'Kenya')
        run_user_lifecycle_smoke(db, admin)
        global_wh, market_a_wh = run_channel_account_smoke(db, admin, market_a, market_b)
        run_first_send_route_smoke(db, admin, market_a, team_a, global_wh, market_a_wh)
        run_unresolved_event_smoke(db, admin, market_a, team_a)
        print('ALL GOVERNANCE ROUND4 SMOKES PASSED')
    finally:
        db.close()
        TMPDIR.cleanup()


if __name__ == '__main__':
    main()
