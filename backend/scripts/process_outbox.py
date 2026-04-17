from __future__ import annotations

from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from app.services.message_dispatch import dispatch_pending_messages


if __name__ == "__main__":
    db = SessionLocal()
    worker_id = f"cli-{uuid.uuid4().hex[:8]}"
    try:
        processed = dispatch_pending_messages(db, worker_id=worker_id)
        print(f"Processed {len(processed)} outbound messages with worker {worker_id}")
    finally:
        db.close()
