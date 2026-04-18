from __future__ import annotations

import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DB_PATH = Path('/tmp/helpdesk_round4.db')
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ.setdefault('APP_ENV', 'development')
os.environ.setdefault('AUTO_INIT_DB', 'true')
os.environ.setdefault('SEED_DEMO_DATA', 'true')
os.environ.setdefault('SECRET_KEY', 'round4-secret')
os.environ.setdefault('DATABASE_URL', f'sqlite:///{DB_PATH}')
os.environ.setdefault('MAX_UPLOAD_BYTES', '64')
os.environ.setdefault('ALLOW_LEGACY_INTEGRATION_API_KEY', 'false')

from fastapi.testclient import TestClient  # noqa: E402
from app.auth_service import hash_secret, hash_password  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.enums import JobStatus, MessageStatus, NoteVisibility, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import BackgroundJob, IntegrationClient, Team, User  # noqa: E402
from app.services.auto_reply_service import fire_and_forget_auto_reply  # noqa: E402
from app.services.message_dispatch import dispatch_pending_messages  # noqa: E402


def login(client: TestClient, username: str, password: str):
    return client.post('/api/auth/login', json={'username': username, 'password': password})


def auth_headers(token: str):
    return {'Authorization': f'Bearer {token}'}


def ensure_cross_team_agent() -> None:
    db = SessionLocal()
    try:
        ops_team = db.query(Team).filter(Team.name == 'Operations').first()
        if not db.query(User).filter(User.username == 'opsagent').first():
            db.add(User(username='opsagent', display_name='Ops Agent', email='opsagent@speedaf.local', password_hash=hash_password('demo123'), role=UserRole.agent, team_id=ops_team.id))
            db.commit()
        if not db.query(IntegrationClient).filter(IntegrationClient.key_id == 'client_round4').first():
            db.add(IntegrationClient(name='Round4 Client', key_id='client_round4', secret_hash=hash_secret('client-secret'), scopes_csv='profile.read,task.write', rate_limit_per_minute=100, is_active=True))
            db.commit()
    finally:
        db.close()


def run() -> None:
    with TestClient(app) as client:
        ensure_cross_team_agent()

        # login throttling
        for _ in range(5):
            resp = login(client, 'blocked-user', 'wrongpw')
            assert resp.status_code == 401, resp.text
        throttled = login(client, 'blocked-user', 'wrongpw')
        assert throttled.status_code == 429, throttled.text

        # successful login from different host not simulated; use agent user for auth flow
        good = login(client, 'agent', 'demo123')
        assert good.status_code == 200, good.text
        lead = login(client, 'lead', 'demo123')
        assert lead.status_code == 200, lead.text
        lead_headers = auth_headers(lead.json()['access_token'])

        # request id and readyz
        ready = client.get('/readyz')
        assert ready.status_code == 200, ready.text
        assert ready.headers.get('X-Request-Id'), ready.headers

        # integration client auth + idempotency
        integ_headers = {'X-Client-Key-Id': 'client_round4', 'X-Client-Key': 'client-secret', 'Idempotency-Key': 'idem-1'}
        payload = {'contact_id': '+18880001111', 'channel': 'whatsapp', 'summary': 'Need escalation', 'description': 'parcel issue'}
        created = client.post('/api/v1/integration/task', headers=integ_headers, json=payload)
        assert created.status_code == 200, created.text
        created2 = client.post('/api/v1/integration/task', headers=integ_headers, json=payload)
        assert created2.status_code == 200, created2.text
        assert created2.json()['case_ref'] == created.json()['case_ref'], created2.text

        # queue outbound and ensure disabled dispatch does not loop forever
        case_resp = client.post('/api/lite/cases', headers=lead_headers, json={'issue_summary': 'reply', 'customer_request': 'hello', 'customer_contact': '+19990001111'})
        assert case_resp.status_code == 200, case_resp.text
        ticket_id = case_resp.json()['case']['id']
        sent = client.post(f'/api/tickets/{ticket_id}/outbound/send', headers=lead_headers, json={'channel': 'whatsapp', 'body': 'hello back'})
        assert sent.status_code == 200, sent.text
        db = SessionLocal()
        try:
            dispatch_pending_messages(db)
            msg = db.query(__import__('app.models', fromlist=['TicketOutboundMessage']).TicketOutboundMessage).filter_by(ticket_id=ticket_id).order_by(__import__('app.models', fromlist=['TicketOutboundMessage']).TicketOutboundMessage.id.desc()).first()
            assert msg.status == MessageStatus.dead, msg.status
            assert msg.failure_code == 'dispatch_disabled', msg.failure_code
        finally:
            db.close()

        # auto reply durable job
        db = SessionLocal()
        try:
            lead_user = db.query(User).filter(User.username == 'lead').first()
            assert lead_user
            fire_and_forget_auto_reply(ticket_id, lead_user.id)
            job = db.query(BackgroundJob).order_by(BackgroundJob.id.desc()).first()
            assert job is not None
            assert job.status == JobStatus.pending, job.status
        finally:
            db.close()

        # upload still works with storage backend
        upload = client.post(
            f'/api/tickets/{ticket_id}/attachments',
            headers=lead_headers,
            files={'file': ('tiny.txt', io.BytesIO(b'hello world'), 'text/plain')},
            data={'visibility': 'external'},
        )
        assert upload.status_code == 200, upload.text

    print('Round4 smoke verification passed')


if __name__ == '__main__':
    run()
