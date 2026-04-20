from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

DB = Path(tempfile.gettempdir()) / 'helpdesk_round6_smoke.db'
if DB.exists():
    DB.unlink()

os.environ['APP_ENV'] = 'development'
os.environ['AUTO_INIT_DB'] = 'false'
os.environ['SEED_DEMO_DATA'] = 'false'
os.environ['DATABASE_URL'] = 'sqlite:///' + str(DB.resolve())
os.environ['SECRET_KEY'] = 'round6-secret'
os.environ['ALLOW_LEGACY_INTEGRATION_API_KEY'] = 'true'
os.environ['INTEGRATION_API_KEY'] = 'round6-legacy-key'
os.environ['MAX_UPLOAD_BYTES'] = '2048'
os.environ['METRICS_ENABLED'] = 'true'

from fastapi.testclient import TestClient  # noqa: E402
from backend.app.auth_service import hash_password, hash_secret  # noqa: E402
from backend.app.db import Base, SessionLocal, engine  # noqa: E402
from backend.app.enums import SourceChannel, TicketPriority, TicketSource, UserRole  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.models import IntegrationClient, Team, User  # noqa: E402
from backend.app.schemas import CustomerInput, TicketCreate  # noqa: E402
from backend.app.services.ticket_service import create_ticket  # noqa: E402

Base.metadata.create_all(bind=engine)
db = SessionLocal()
support = Team(name='Support', team_type='support')
db.add(support); db.commit(); db.refresh(support)
admin = User(username='admin', display_name='Admin', email='admin@test.local', password_hash=hash_password('pw'), role=UserRole.admin, team_id=support.id)
agent = User(username='agent', display_name='Agent', email='agent@test.local', password_hash=hash_password('pw'), role=UserRole.agent, team_id=support.id)
db.add_all([admin, agent]); db.commit(); db.refresh(admin); db.refresh(agent)
client_row = IntegrationClient(name='ops-bot', key_id='ops-bot', secret_hash=hash_secret('bot-secret'), scopes_csv='profile.read,task.write', rate_limit_per_minute=60)
db.add(client_row); db.commit()
ticket = create_ticket(
    db,
    TicketCreate(
        title='Need help',
        description='Package delayed',
        source=TicketSource.ai_intake,
        source_channel=SourceChannel.whatsapp,
        priority=TicketPriority.high,
        customer=CustomerInput(name='Alice', phone='+10000000001'),
        team_id=support.id,
        assignee_id=agent.id,
    ),
    admin,
)
client = TestClient(app)

def login(username: str):
    res = client.post('/api/auth/login', json={'username': username, 'password': 'pw'})
    assert res.status_code == 200, res.text
    token = res.json()['access_token']
    return {'Authorization': f'Bearer {token}'}

admin_headers = login('admin')
agent_headers = login('agent')

# Admin capability catalog and override APIs
res = client.get('/api/admin/capabilities/catalog', headers=admin_headers)
assert res.status_code == 200, res.text
assert 'ticket.close' in res.json(), res.text
res = client.get(f'/api/admin/users/{agent.id}/capabilities', headers=admin_headers)
assert res.status_code == 200, res.text
res = client.put(f'/api/admin/users/{agent.id}/capabilities/ticket.close', json={'capability': 'ticket.close', 'allowed': True}, headers=admin_headers)
assert res.status_code == 200, res.text

# agent can now cancel after override on a valid transition
res = client.post(f'/api/tickets/{ticket.id}/status', json={'new_status': 'canceled', 'note': 'done'}, headers=agent_headers)
assert res.status_code == 200, res.text
assert res.json()['status'] == 'canceled', res.text

# Integration client auth path works
res = client.get('/api/v1/integration/profile/+10000000001', headers={'X-Client-Key-Id': 'ops-bot', 'X-Client-Key': 'bot-secret'})
assert res.status_code == 200, res.text
assert res.json()['ok'] is True, res.text

# Metrics and readyz exist
assert client.get('/metrics').status_code == 200
assert client.get('/readyz').status_code == 200

# Production settings reject sqlite / legacy shortcuts
code = """
import os
os.environ['APP_ENV']='production'
os.environ['SECRET_KEY']='prod-secret'
os.environ['DATABASE_URL']='sqlite:///bad.db'
os.environ['ALLOWED_ORIGINS']='https://ops.example.com'
from backend.app.settings import Settings
Settings()
"""
proc = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
assert proc.returncode != 0, proc.stdout + proc.stderr
assert 'PostgreSQL' in (proc.stderr + proc.stdout)

print('ROUND6_SMOKE_PASSED')
