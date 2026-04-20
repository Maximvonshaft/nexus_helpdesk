from __future__ import annotations

import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DB_PATH = Path('/tmp/helpdesk_round3.db')
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("AUTO_INIT_DB", "true")
os.environ.setdefault("SEED_DEMO_DATA", "true")
os.environ.setdefault("SECRET_KEY", "round3-secret")
os.environ.setdefault("INTEGRATION_API_KEY", "round3-int-key")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{DB_PATH}")
os.environ.setdefault("MAX_UPLOAD_BYTES", "64")

from fastapi.testclient import TestClient  # noqa: E402
from app.auth_service import hash_password  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Team, TicketAttachment, User  # noqa: E402
from app.settings import get_settings  # noqa: E402


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


def run() -> None:
    with TestClient(app) as client:
        ensure_cross_team_agent()
        lead_headers = login(client, 'lead', 'demo123')
        agent_headers = login(client, 'agent', 'demo123')
        ops_headers = login(client, 'opsagent', 'demo123')

        create_case = client.post('/api/lite/cases', headers=lead_headers, json={
            'issue_summary': 'round3 case',
            'customer_request': 'please inspect',
            'customer_contact': '+15550002222',
        })
        assert create_case.status_code == 200, create_case.text
        case_id = create_case.json()['case']['id']

        # assign to support agent
        db = SessionLocal()
        try:
            lead_user = db.query(User).filter(User.username == 'lead').first()
            agent_user = db.query(User).filter(User.username == 'agent').first()
            ops_user = db.query(User).filter(User.username == 'opsagent').first()
            support_team = db.query(Team).filter(Team.name == 'Support').first()
            assert lead_user and agent_user and ops_user and support_team
            lead_user_id = lead_user.id
            agent_user_id = agent_user.id
            ops_user_id = ops_user.id
            support_team_id = support_team.id
        finally:
            db.close()

        res = client.post(f'/api/lite/cases/{case_id}/workflow-update', headers=lead_headers, json={'assignee_id': agent_user_id})
        assert res.status_code == 200, res.text
        assert res.json()['assigned_to'] == 'Agent One', res.text

        # team-only update must not clear assignee
        res = client.post(f'/api/lite/cases/{case_id}/workflow-update', headers=lead_headers, json={'team_id': support_team_id})
        assert res.status_code == 200, res.text
        assert res.json()['assigned_to'] == 'Agent One', res.text

        # cross-team assign must fail
        res = client.post(f'/api/lite/cases/{case_id}/workflow-update', headers=lead_headers, json={'assignee_id': ops_user_id})
        assert res.status_code == 400, res.text

        # explicit status should survive assignment auto-advance logic
        create_case2 = client.post('/api/lite/cases', headers=lead_headers, json={
            'issue_summary': 'round3 case 2',
            'customer_request': 'status preserve',
            'customer_contact': '+15550003333',
        })
        assert create_case2.status_code == 200, create_case2.text
        case2_id = create_case2.json()['case']['id']
        detail = client.get(f'/api/lite/cases/{case2_id}', headers=lead_headers)
        assert detail.status_code == 200, detail.text
        initial_status = detail.json()['status']
        res = client.post(f'/api/lite/cases/{case2_id}/workflow-update', headers=lead_headers, json={'assignee_id': lead_user_id, 'status': initial_status})
        assert res.status_code == 200, res.text
        assert res.json()['status'] == initial_status, res.text

        # strict contract
        res = client.post(f'/api/lite/cases/{case_id}/workflow-update', headers=lead_headers, json={'required_action': 'ok', 'typo_field': 'oops'})
        assert res.status_code == 422, res.text

        # lite meta limited by team
        res = client.get('/api/lite/meta', headers=agent_headers)
        assert res.status_code == 200, res.text
        usernames = {u['username'] for u in res.json()['users']}
        assert 'opsagent' not in usernames, res.text
        assert 'agent' in usernames, res.text

        # upload internal attachment and deny cross-team access
        upload = client.post(
            f'/api/tickets/{case_id}/attachments',
            headers=lead_headers,
            files={'file': ('tiny.txt', io.BytesIO(b'abc'), 'text/plain')},
            data={'visibility': 'internal'},
        )
        assert upload.status_code == 200, upload.text
        attachment_url = upload.json()['download_url']
        denied = client.get(attachment_url, headers=ops_headers)
        assert denied.status_code == 403, denied.text

        # customer existence should not leak across teams
        ticket = client.get(f'/api/tickets/{case_id}', headers=lead_headers)
        assert ticket.status_code == 200, ticket.text
        customer_id = ticket.json()['customer']['id']
        hidden = client.get(f'/api/customers/{customer_id}/history', headers=ops_headers)
        assert hidden.status_code == 404, hidden.text

        # timestamps should serialize with timezone marker
        res = client.get(f'/api/tickets/{case_id}', headers=lead_headers)
        assert res.status_code == 200, res.text
        assert res.json()['created_at'].endswith('Z'), res.text
        assert res.json()['updated_at'].endswith('Z'), res.text

    print('Round3 smoke verification passed')


if __name__ == '__main__':
    run()
