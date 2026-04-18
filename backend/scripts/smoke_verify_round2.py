from __future__ import annotations

import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DB_PATH = Path('/tmp/helpdesk_round2.db')
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("AUTO_INIT_DB", "true")
os.environ.setdefault("SEED_DEMO_DATA", "true")
os.environ.setdefault("SECRET_KEY", "round2-secret")
os.environ.setdefault("INTEGRATION_API_KEY", "round2-int-key")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{DB_PATH}")
os.environ.setdefault("MAX_UPLOAD_BYTES", "16")

from fastapi.testclient import TestClient  # noqa: E402
from app.auth_service import hash_password  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.enums import TicketPriority, TicketSource, SourceChannel, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Team, TicketEvent, User  # noqa: E402


def login(client: TestClient, username: str, password: str) -> dict[str, str]:
    resp = client.post('/api/auth/login', json={'username': username, 'password': password})
    assert resp.status_code == 200, resp.text
    token = resp.json()['access_token']
    return {'Authorization': f'Bearer {token}'}


def ensure_cross_team_agent() -> None:
    db = SessionLocal()
    try:
        ops_team = db.query(Team).filter(Team.name == 'Operations').first()
        assert ops_team is not None
        existing = db.query(User).filter(User.username == 'opsagent').first()
        if not existing:
            db.add(User(
                username='opsagent',
                display_name='Ops Agent',
                email='opsagent@speedaf.local',
                password_hash=hash_password('demo123'),
                role=UserRole.agent,
                team_id=ops_team.id,
            ))
            db.commit()
    finally:
        db.close()


def create_overdue_ticket(client: TestClient, headers: dict[str, str]) -> int:
    db = SessionLocal()
    try:
        support = db.query(Team).filter(Team.name == 'Support').first()
        resp = client.post('/api/tickets', headers=headers, json={
            'title': 'Overdue test',
            'description': 'Needs breach check',
            'source': 'manual',
            'source_channel': 'internal',
            'priority': 'low',
            'team_id': support.id,
        })
        assert resp.status_code == 200, resp.text
        return resp.json()['id']
    finally:
        db.close()


def set_ticket_overdue(ticket_id: int) -> tuple[int, int]:
    from app.models import Ticket
    from app.utils.time import utc_now
    from datetime import timedelta
    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
        assert ticket is not None
        ticket.resolution_due_at = utc_now() - timedelta(days=1)
        ticket.first_response_due_at = utc_now() - timedelta(days=1)
        ticket.resolution_breached = False
        ticket.first_response_breached = False
        db.commit()
        before = db.query(TicketEvent).filter(TicketEvent.ticket_id == ticket_id).count()
        return ticket_id, before
    finally:
        db.close()


def assert_no_get_side_effect(client: TestClient, headers: dict[str, str], ticket_id: int, before_events: int) -> None:
    resp = client.get('/api/tickets', headers=headers)
    assert resp.status_code == 200, resp.text
    db = SessionLocal()
    try:
        after = db.query(TicketEvent).filter(TicketEvent.ticket_id == ticket_id).count()
        assert after == before_events, f'GET /api/tickets mutated events: {before_events} -> {after}'
    finally:
        db.close()


def run() -> None:
    with TestClient(app) as client:
        ensure_cross_team_agent()
        lead_headers = login(client, 'lead', 'demo123')
        ops_headers = login(client, 'opsagent', 'demo123')

        create_case = client.post('/api/lite/cases', headers=lead_headers, json={
            'issue_summary': 'cross team attachment',
            'customer_request': 'please inspect',
            'customer_contact': '+15550001111',
        })
        assert create_case.status_code == 200, create_case.text
        case_id = create_case.json()['case']['id']

        upload = client.post(
            f'/api/tickets/{case_id}/attachments',
            headers=lead_headers,
            files={'file': ('tiny.txt', io.BytesIO(b'0123456789abcde'), 'text/plain')},
            data={'visibility': 'internal'},
        )
        assert upload.status_code == 200, upload.text
        attachment_url = upload.json()['download_url']
        denied = client.get(attachment_url, headers=ops_headers)
        assert denied.status_code == 403, denied.text

        too_big = client.post(
            f'/api/tickets/{case_id}/attachments',
            headers=lead_headers,
            files={'file': ('huge.txt', io.BytesIO(b'0123456789abcdefg'), 'text/plain')},
            data={'visibility': 'external'},
        )
        assert too_big.status_code == 413, too_big.text

        db = SessionLocal()
        try:
            lead_user = db.query(User).filter(User.username == 'lead').first()
            lead_user_id = lead_user.id
        finally:
            db.close()

        failed_workflow = client.post(
            f'/api/lite/cases/{case_id}/workflow-update',
            headers=lead_headers,
            json={'required_action': 'should_not_persist', 'status': 'closed'},
        )
        assert failed_workflow.status_code == 400, failed_workflow.text
        case_after_fail = client.get(f'/api/lite/cases/{case_id}', headers=lead_headers)
        assert case_after_fail.status_code == 200, case_after_fail.text
        assert case_after_fail.json()['required_action'] != 'should_not_persist'

        ok_workflow = client.post(
            f'/api/lite/cases/{case_id}/workflow-update',
            headers=lead_headers,
            json={'required_action': 'call customer', 'human_note': 'checking owner', 'assignee_id': lead_user_id, 'status': 'waiting_customer'},
        )
        assert ok_workflow.status_code == 200, ok_workflow.text
        assert ok_workflow.json()['required_action'] == 'call customer'
        assert ok_workflow.json()['status'] == 'waiting_customer'

        overdue_ticket_id = create_overdue_ticket(client, lead_headers)
        overdue_ticket_id, before_events = set_ticket_overdue(overdue_ticket_id)
        assert_no_get_side_effect(client, lead_headers, overdue_ticket_id, before_events)

    print('Round2 smoke verification passed')


if __name__ == '__main__':
    run()
