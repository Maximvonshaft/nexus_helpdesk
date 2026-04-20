from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

db_path = Path(tempfile.gettempdir()) / 'helpdesk_round8_smoke.db'
if db_path.exists():
    db_path.unlink()

mcp_send_log = Path(tempfile.gettempdir()) / 'helpdesk_round8_mcp_send.jsonl'
if mcp_send_log.exists():
    mcp_send_log.unlink()

fake_mcp = Path(tempfile.gettempdir()) / 'fake_openclaw_mcp_server.py'
fake_mcp.write_text(textwrap.dedent(f'''
#!/usr/bin/env python3
import json, os, sys
LOG = {str(mcp_send_log)!r}
conversations = {{
    "wa-session-1": {{
        "session_key": "wa-session-1",
        "channel": "whatsapp",
        "recipient": "+639111111111",
        "accountId": "acct-1",
        "threadId": None,
        "route": {{"channel": "whatsapp", "recipient": "+639111111111", "accountId": "acct-1"}},
    }}
}}
messages = {{
    "wa-session-1": [
        {{"id": "msg-1", "role": "user", "author": "Customer", "text": "Where is my parcel?"}},
        {{"id": "msg-2", "role": "assistant", "author": "AI", "text": "We are checking it."}},
    ]
}}

def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\\n")
    sys.stdout.flush()

for line in sys.stdin:
    if not line.strip():
        continue
    req = json.loads(line)
    method = req.get('method')
    req_id = req.get('id')
    if method == 'initialize':
        send({{'jsonrpc': '2.0', 'id': req_id, 'result': {{'protocolVersion': '2024-11-05', 'serverInfo': {{'name': 'fake-openclaw', 'version': '1.0'}}, 'capabilities': {{}}}}}})
        continue
    if method == 'notifications/initialized':
        continue
    if method == 'tools/call':
        name = req['params']['name']
        args = req['params'].get('arguments', {{}})
        if name == 'conversations_list':
            result = {{'structuredContent': {{'conversations': list(conversations.values())}}}}
        elif name == 'conversation_get':
            result = {{'structuredContent': conversations[args['session_key']]}}
        elif name == 'messages_read':
            result = {{'structuredContent': {{'messages': messages.get(args['session_key'], [])}}}}
        elif name == 'messages_send':
            with open(LOG, 'a', encoding='utf-8') as fh:
                fh.write(json.dumps({{'session_key': args['session_key'], 'text': args['text']}}, ensure_ascii=False) + "\\n")
            result = {{'structuredContent': {{'ok': True}}}}
        elif name == 'attachments_fetch':
            result = {{'structuredContent': {{'attachments': []}}}}
        elif name == 'events_poll':
            result = {{'structuredContent': {{'events': []}}}}
        else:
            result = {{'structuredContent': {{}}}}
        send({{'jsonrpc': '2.0', 'id': req_id, 'result': result}})
''').strip() + '\n', encoding='utf-8')
fake_mcp.chmod(fake_mcp.stat().st_mode | stat.S_IEXEC)

os.environ['APP_ENV'] = 'development'
os.environ['AUTO_INIT_DB'] = 'false'
os.environ['SEED_DEMO_DATA'] = 'false'
os.environ['DATABASE_URL'] = 'sqlite:///' + str(db_path.resolve())
os.environ['SECRET_KEY'] = 'round8-secret'
os.environ['ENABLE_OUTBOUND_DISPATCH'] = 'true'
os.environ['OUTBOUND_PROVIDER'] = 'openclaw'
os.environ['OPENCLAW_TRANSPORT'] = 'mcp'
os.environ['OPENCLAW_MCP_COMMAND'] = str(fake_mcp)
os.environ['OPENCLAW_MCP_CLAUDE_CHANNEL_MODE'] = 'off'

from fastapi.testclient import TestClient  # noqa: E402
from backend.app.auth_service import hash_password  # noqa: E402
from backend.app.db import Base, SessionLocal, engine  # noqa: E402
from backend.app.enums import SourceChannel, TicketPriority, TicketSource, UserRole  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.models import Market, Team, User  # noqa: E402
from backend.app.schemas import CustomerInput, OutboundSendRequest, TicketCreate  # noqa: E402
from backend.app.services.message_dispatch import dispatch_pending_messages  # noqa: E402
from backend.app.services.openclaw_bridge import link_ticket_to_openclaw_session, sync_openclaw_conversation  # noqa: E402
from backend.app.services.ticket_service import create_ticket, get_ticket_or_404, send_outbound_message  # noqa: E402

Base.metadata.create_all(bind=engine)
db = SessionLocal()
market = Market(code='PH', name='Philippines', country_code='PH', language_code='en', timezone='Asia/Manila')
db.add(market)
db.commit(); db.refresh(market)
team = Team(name='PH Support', team_type='support', market_id=market.id)
admin = User(username='admin', display_name='Admin', email='admin@test.local', password_hash=hash_password('pw'), role=UserRole.admin, team_id=None)
lead = User(username='lead', display_name='Lead', email='lead@test.local', password_hash=hash_password('pw'), role=UserRole.lead, team_id=None)
agent = User(username='agent', display_name='Agent', email='agent@test.local', password_hash=hash_password('pw'), role=UserRole.agent, team_id=None)
db.add_all([team, admin, lead, agent]); db.commit(); db.refresh(team)
lead.team_id = team.id
agent.team_id = team.id
db.commit(); db.refresh(lead); db.refresh(agent)

ticket = create_ticket(
    db,
    TicketCreate(
        title='Customer inquiry',
        description='Inbound from OpenClaw',
        source=TicketSource.api,
        source_channel=SourceChannel.whatsapp,
        priority=TicketPriority.medium,
        team_id=team.id,
        market_id=market.id,
        country_code='PH',
        customer=CustomerInput(name='Customer', phone='+639111111111'),
        source_chat_id='+639111111111',
        preferred_reply_channel='whatsapp',
        preferred_reply_contact='+639111111111',
    ),
    lead,
)
link = link_ticket_to_openclaw_session(db, ticket_id=ticket.id, session_key='wa-session-1', channel='whatsapp', recipient='+639111111111', account_id='acct-1', route={'channel': 'whatsapp', 'recipient': '+639111111111', 'accountId': 'acct-1'})
assert link.session_key == 'wa-session-1'
result = sync_openclaw_conversation(db, ticket_id=ticket.id, session_key='wa-session-1', limit=10)
assert result.linked_ticket_id == ticket.id
assert len(result.messages) >= 2
fresh = get_ticket_or_404(db, ticket.id)
assert fresh.last_customer_message == 'Where is my parcel?'

msg = send_outbound_message(db, ticket.id, OutboundSendRequest(channel=SourceChannel.whatsapp, body='Your parcel will arrive tomorrow.'), lead)
processed = dispatch_pending_messages(db, limit=10, worker_id='smoke-round8')
assert processed and processed[0].status.value == 'sent'
log_lines = mcp_send_log.read_text(encoding='utf-8').strip().splitlines()
assert log_lines, 'Expected fake MCP send log'
last_send = json.loads(log_lines[-1])
assert last_send['session_key'] == 'wa-session-1'
assert 'tomorrow' in last_send['text']

client = TestClient(app)
res = client.post('/api/auth/login', json={'username': 'admin', 'password': 'pw'})
assert res.status_code == 200, res.text
headers = {'Authorization': 'Bearer ' + res.json()['access_token']}
res = client.get('/api/admin/markets', headers=headers)
assert res.status_code == 200, res.text
assert res.json()[0]['code'] == 'PH', res.text
res = client.get('/api/tickets/' + str(ticket.id), headers=headers)
assert res.status_code == 200, res.text
body = res.json()
assert body['market_id'] == market.id, res.text
assert body['country_code'] == 'PH', res.text
assert body['openclaw_conversation']['session_key'] == 'wa-session-1', res.text
assert len(body['openclaw_transcript']) >= 2, res.text

print('ROUND8_SMOKE_PASSED')
