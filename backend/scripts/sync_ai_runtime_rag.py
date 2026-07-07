from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.services.ai_runtime_rag_sync import DEFAULT_UPSERT_PATH, sync_runtime_rag  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync published Nexus customer knowledge to the private AI Runtime RAG index.")
    parser.add_argument("--base-url", default=None, help="AI Runtime base URL. Defaults to PRIVATE_AI_RUNTIME_BASE_URL.")
    parser.add_argument("--token-file", default=None, help="Bearer token file. Defaults to PRIVATE_AI_RUNTIME_TOKEN_FILE.")
    parser.add_argument("--upsert-path", default=DEFAULT_UPSERT_PATH)
    parser.add_argument("--item-key-prefix", default=None, help="Optional KnowledgeItem item_key prefix filter.")
    parser.add_argument("--include-internal", action="store_true", help="Also sync items explicitly marked customer_visible=false.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        result = sync_runtime_rag(
            db,
            base_url=args.base_url,
            token_file=args.token_file,
            upsert_path=args.upsert_path,
            item_key_prefix=args.item_key_prefix,
            include_internal=args.include_internal,
            limit=args.limit or None,
            batch_size=args.batch_size,
            timeout_seconds=args.timeout,
            dry_run=args.dry_run,
        )
        if result.ok and not args.dry_run:
            db.commit()
        else:
            db.rollback()
        print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if result.ok else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
