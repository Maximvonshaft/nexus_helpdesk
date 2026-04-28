from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from sqlalchemy import or_

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.db import SessionLocal
from backend.app.models import Ticket
from backend.app.services.openclaw_bridge import link_ticket_to_openclaw_session, sync_openclaw_conversation
from backend.app.services.openclaw_client_factory import get_openclaw_runtime_client


def _contact_variants(value: object) -> set[str]:
    raw = str(value or '').strip()
    if not raw:
        return set()
    variants = {raw}
    lowered = raw.lower()
    for marker in ('agent:support:whatsapp:direct:', 'whatsapp:', 'wa:', 'direct:'):
        if marker in lowered:
            suffix = raw[lowered.rfind(marker) + len(marker):].strip()
            if suffix:
                variants.add(suffix)
    digits = re.sub(r'\D+', '', raw)
    if digits:
        variants.add(digits)
        variants.add(f'+{digits}')
    return {item for item in variants if item}


def _extract_route(item: dict) -> dict:
    route = item.get('route') if isinstance(item.get('route'), dict) else {}
    merged = dict(route)
    for src, dst in (
        ('channel', 'channel'),
        ('recipient', 'recipient'),
        ('to', 'recipient'),
        ('accountId', 'accountId'),
        ('account_id', 'accountId'),
        ('threadId', 'threadId'),
        ('thread_id', 'threadId'),
    ):
        value = item.get(src)
        if value not in (None, '') and dst not in merged:
            merged[dst] = value
    return merged


def _extract_session_key(item: dict) -> str | None:
    for key in ('session_key', 'sessionKey', 'key', 'id'):
        value = item.get(key)
        if value not in (None, ''):
            return str(value)
    return None


def _find_matching_ticket(db, *, recipient: str | None, session_key: str | None) -> Ticket | None:
    variants = set()
    variants.update(_contact_variants(recipient))
    variants.update(_contact_variants(session_key))
    if not variants:
        return None
    return (
        db.query(Ticket)
        .filter(or_(Ticket.source_chat_id.in_(variants), Ticket.preferred_reply_contact.in_(variants)))
        .order_by(Ticket.updated_at.desc())
        .first()
    )


def _as_conversation_items(payload) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ('conversations', 'sessions', 'items', 'results'):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description='Sync OpenClaw conversations into NexusDesk tickets')
    parser.add_argument('--session-key', help='Specific OpenClaw session key to sync')
    parser.add_argument('--ticket-id', type=int, help='Ticket id to link/sync')
    parser.add_argument('--limit', type=int, default=50)
    parser.add_argument('--agent', default='support')
    args = parser.parse_args()

    db = SessionLocal()
    try:
        with get_openclaw_runtime_client() as client:
            if args.session_key and args.ticket_id:
                result = sync_openclaw_conversation(
                    db,
                    ticket_id=args.ticket_id,
                    session_key=args.session_key,
                    limit=args.limit,
                    client=client,
                )
                print(json.dumps(result.model_dump(mode='json'), ensure_ascii=False, indent=2))
                return 0

            conversations = client.conversations_list(limit=args.limit, agent=args.agent)
            items = _as_conversation_items(conversations)
            synced = 0
            unmatched = 0
            skipped = 0
            errors: list[dict] = []

            for item in items:
                session_key = _extract_session_key(item)
                route = _extract_route(item)
                recipient = route.get('recipient') or item.get('recipient') or item.get('to')
                if not session_key:
                    skipped += 1
                    continue
                ticket = _find_matching_ticket(db, recipient=str(recipient or ''), session_key=session_key)
                if not ticket:
                    unmatched += 1
                    continue
                try:
                    link_ticket_to_openclaw_session(
                        db,
                        ticket_id=ticket.id,
                        session_key=session_key,
                        route=route if route else None,
                        channel=route.get('channel') or item.get('channel'),
                        recipient=recipient,
                        account_id=route.get('accountId') or item.get('accountId') or item.get('account_id'),
                        thread_id=route.get('threadId') or item.get('threadId') or item.get('thread_id'),
                    )
                    sync_openclaw_conversation(db, ticket_id=ticket.id, session_key=session_key, limit=args.limit, client=client)
                    db.commit()
                    synced += 1
                except Exception as exc:
                    db.rollback()
                    errors.append({'session_key': session_key, 'ticket_id': ticket.id, 'error': str(exc)[:500]})

            response = {
                'ok': True,
                'synced': synced,
                'unmatched': unmatched,
                'skipped': skipped,
                'total_seen': len(items),
            }
            if unmatched and synced == 0:
                response['reason'] = 'no_matching_ticket'
            if errors:
                response['errors'] = errors
            print(json.dumps(response, ensure_ascii=False))
            return 0
    finally:
        db.close()


if __name__ == '__main__':
    raise SystemExit(main())
