from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.db import SessionLocal
from backend.app.services.openclaw_bridge import sync_openclaw_inbound_conversations_once


def main() -> int:
    db = SessionLocal()
    try:
        summary = sync_openclaw_inbound_conversations_once(db, source='default', force=True)
        db.commit()
        result = {
            'ok': bool(summary.get('ok', True)),
            'conversations_seen': int(summary.get('conversations_seen', 0)),
            'conversations_skipped': int(summary.get('conversations_skipped', 0)),
            'tickets_created': int(summary.get('tickets_created', 0)),
            'links_created': int(summary.get('links_created', 0)),
            'messages_inserted': int(summary.get('messages_inserted', 0)),
            'unresolved_events': int(summary.get('unresolved_events', 0)),
            'errors': summary.get('errors', []),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['ok'] else 1
    except Exception as exc:
        db.rollback()
        print(json.dumps({
            'ok': False,
            'conversations_seen': 0,
            'conversations_skipped': 0,
            'tickets_created': 0,
            'links_created': 0,
            'messages_inserted': 0,
            'unresolved_events': 0,
            'errors': [str(exc)],
        }, ensure_ascii=False, indent=2))
        return 1
    finally:
        db.close()


if __name__ == '__main__':
    raise SystemExit(main())
