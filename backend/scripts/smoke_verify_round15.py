from __future__ import annotations

import os
import sys
import tempfile
import subprocess
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ['APP_ENV'] = 'development'
db_path = Path(tempfile.gettempdir()) / 'helpdesk_round15_smoke.db'
if db_path.exists():
    db_path.unlink()
os.environ['DATABASE_URL'] = 'sqlite:///' + str(db_path.resolve())
os.environ['AUTO_INIT_DB'] = 'false'
os.environ['SEED_DEMO_DATA'] = 'false'
os.environ['SECRET_KEY'] = 'round15-secret'
os.environ['OPENCLAW_SYNC_ENABLED'] = 'true'

from fastapi.testclient import TestClient  # noqa: E402
from backend.app.auth_service import hash_password  # noqa: E402
from backend.app.db import SessionLocal, engine  # noqa: E402
from backend.app.enums import SourceChannel, TicketPriority, TicketSource, UserRole  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.models import ChannelAccount, Market, Team, User  # noqa: E402
from backend.app.schemas import CustomerInput, TicketCreate  # noqa: E402
from backend.app.services.openclaw_bridge import ensure_openclaw_conversation_link, persist_openclaw_attachment_reference  # noqa: E402
from backend.app.services.ticket_service import create_ticket, get_ticket_or_404  # noqa: E402
from backend.app.unit_of_work import managed_session  # noqa: E402

alembic_cmd = [sys.executable, '-m', 'alembic']
if shutil.which('alembic'):
    alembic_cmd = ['alembic']
result = subprocess.run(
    [*alembic_cmd, '-c', str(ROOT / 'backend' / 'alembic.ini'), 'upgrade', 'head'],
    cwd=str(ROOT / 'backend'),
    capture_output=True,
    text=True,
)
assert result.returncode == 0, result.stderr
with SessionLocal() as db:
    market = Market(code='PH', name='Philippines', country_code='PH', language_code='en')
    team = Team(name='PH Support', team_type='support', market=market)
    admin = User(username='admin', display_name='Admin', email='admin@test.local', password_hash=hash_password('pw'), role=UserRole.admin, team=team)
    db.add_all([market, team, admin]); db.commit(); db.refresh(market); db.refresh(team); db.refresh(admin)
    channel_account = ChannelAccount(provider='whatsapp', account_id='wa-main-ph', display_name='PH Main', market_id=market.id, priority=10)
    db.add(channel_account); db.commit(); db.refresh(channel_account)

    with managed_session(db):
        ticket = create_ticket(db, TicketCreate(
            title='Delay', description='Package delayed', source=TicketSource.ai_intake, source_channel=SourceChannel.whatsapp,
            priority=TicketPriority.medium, customer=CustomerInput(name='Alice', phone='+10000000001'), team_id=team.id, market_id=market.id, country_code='PH'
        ), admin)
        ensure_openclaw_conversation_link(db, ticket=ticket, session_key='agent:support:whatsapp:dm:+10000000001', route={'channel':'whatsapp','recipient':'+10000000001','accountId':'wa-main-ph'})
        db.flush()
        tid = ticket.id

client = TestClient(app)
res = client.post('/api/auth/login', json={'username':'admin','password':'pw'})
assert res.status_code == 200, res.text
headers = {'Authorization': f"Bearer {res.json()['access_token']}"}

# create bulletin
res = client.post('/api/admin/bulletins', headers=headers, json={
    'market_id': 1,
    'country_code': 'PH',
    'title': 'Typhoon Delay Notice',
    'body': 'Severe weather may delay deliveries by 1-2 days.',
    'summary': 'Weather delays possible',
    'category': 'public_event',
    'severity': 'warning',
    'auto_inject_to_ai': True,
    'is_active': True,
})
assert res.status_code == 200, res.text

# verify ticket detail includes bulletin and conversation fields
res = client.get(f'/api/tickets/{tid}', headers=headers)
assert res.status_code == 200, res.text
body = res.json()
assert body['market_code'] == 'PH', body
assert body['country_code'] == 'PH', body
assert body['active_market_bulletins'], body
assert body['openclaw_conversation']['account_id'] == 'wa-main-ph', body

# attachment metadata fallback persistence should work
from backend.app.models import OpenClawAttachmentReference
with SessionLocal() as db:
    ref = OpenClawAttachmentReference(ticket_id=tid, conversation_id=1, transcript_message_id=1, remote_attachment_id='att-1', content_type='text/plain', filename='note.txt', metadata_json={'text':'hello attachment'}, storage_status='referenced')
    db.add(ref); db.commit(); db.refresh(ref)
    with managed_session(db):
        persist_openclaw_attachment_reference(db, attachment_ref=ref)
        db.flush()
    db.refresh(ref)
    assert ref.storage_status == 'captured'

print('ROUND15_SMOKE_PASSED')
