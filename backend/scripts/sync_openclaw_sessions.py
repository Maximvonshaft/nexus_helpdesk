from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.db import SessionLocal
from backend.app.models import OpenClawConversationLink, Ticket
from backend.app.services.openclaw_bridge import link_ticket_to_openclaw_session, sync_openclaw_conversation
from backend.app.services.openclaw_mcp_client import OpenClawMCPClient


def main() -> int:
    parser = argparse.ArgumentParser(description='Sync OpenClaw conversations into NexusDesk tickets')
    parser.add_argument('--session-key', help='Specific OpenClaw session key to sync')
    parser.add_argument('--ticket-id', type=int, help='Ticket id to link/sync')
    parser.add_argument('--limit', type=int, default=50)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.session_key and args.ticket_id:
            result = sync_openclaw_conversation(db, ticket_id=args.ticket_id, session_key=args.session_key, limit=args.limit)
            print(json.dumps(result.model_dump(mode='json'), ensure_ascii=False, indent=2))
            return 0

        with OpenClawMCPClient() as client:
            conversations = client.conversations_list(limit=args.limit)
        items = conversations if isinstance(conversations, list) else conversations.get('conversations', [])
        synced = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            session_key = item.get('session_key') or item.get('sessionKey')
            recipient = item.get('recipient') or (item.get('route') or {}).get('recipient')
            if not session_key or not recipient:
                continue
            ticket = db.query(Ticket).filter((Ticket.source_chat_id == recipient) | (Ticket.preferred_reply_contact == recipient)).order_by(Ticket.updated_at.desc()).first()
            if not ticket:
                continue
            link_ticket_to_openclaw_session(db, ticket_id=ticket.id, session_key=session_key, route=item.get('route') if isinstance(item.get('route'), dict) else None, channel=item.get('channel'), recipient=recipient, account_id=item.get('accountId'), thread_id=item.get('threadId'))
            sync_openclaw_conversation(db, ticket_id=ticket.id, session_key=session_key, limit=args.limit)
            synced += 1
        print(json.dumps({'ok': True, 'synced': synced}, ensure_ascii=False))
        return 0
    finally:
        db.close()


if __name__ == '__main__':
    raise SystemExit(main())
