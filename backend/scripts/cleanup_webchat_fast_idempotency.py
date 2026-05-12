from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import db_context  # noqa: E402
from app.services.webchat_fast_idempotency_db import cleanup_expired_webchat_fast_idempotency  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Cleanup expired WebChat Fast Lane V2.2.2 idempotency rows")
    parser.add_argument("--dry-run", action="store_true", help="Only print the SQL-safe action description; do not delete rows")
    args = parser.parse_args()
    if args.dry_run:
        print("dry_run=true action=delete rows where webchat_fast_idempotency.expires_at < now()")
        return 0
    with db_context() as db:
        deleted = cleanup_expired_webchat_fast_idempotency(db)
    print(f"deleted={deleted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
