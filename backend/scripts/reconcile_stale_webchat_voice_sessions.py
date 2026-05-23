from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import SessionLocal
from app.services.webchat_voice_session_reconciler import reconcile_stale_webchat_voice_sessions


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reconcile stale non-terminal WebChat voice sessions.")
    parser.add_argument("--apply", action="store_true", help="Persist updates. Default is dry-run.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum rows to inspect/update, 1..1000.")
    parser.add_argument("--older-than-seconds", type=int, default=300, help="Grace period after expires_at before a row is eligible.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    db = SessionLocal()
    try:
        result = reconcile_stale_webchat_voice_sessions(
            db,
            dry_run=not args.apply,
            limit=args.limit,
            older_than_seconds=args.older_than_seconds,
        )
        if args.apply:
            db.commit()
        else:
            db.rollback()
        payload = result.to_safe_dict()
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            mode = "apply" if args.apply else "dry-run"
            print(
                f"ok={payload['ok']} mode={mode} eligible={payload['eligible_count']} "
                f"processed={payload['processed_count']} updated={payload['updated_count']} "
                f"skipped={payload['skipped_count']}"
            )
        return 0
    except Exception as exc:
        db.rollback()
        if args.json:
            print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        else:
            print(f"ok=False error={type(exc).__name__} detail={exc}", file=sys.stderr)
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
