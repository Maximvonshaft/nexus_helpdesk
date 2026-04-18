from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.services.observability import configure_logging, record_worker_poll, record_worker_result  # noqa: E402
from app.services.openclaw_bridge import consume_openclaw_events_once  # noqa: E402
from app.services.heartbeat_service import update_service_heartbeat  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.unit_of_work import managed_session  # noqa: E402

settings = get_settings()
configure_logging()
logger = logging.getLogger("openclaw_event_daemon")


def main() -> int:
    worker_id = f"openclaw-events-{os.getpid()}"
    logger.info("openclaw event daemon started", extra={"worker_id": worker_id})
    while True:
        with SessionLocal() as db:
            try:
                with managed_session(db):
                    processed = consume_openclaw_events_once(db, timeout_seconds=settings.openclaw_sync_poll_timeout_seconds)
                    update_service_heartbeat(db, service_name='openclaw_event_daemon', instance_id=worker_id, status='ok', details={'processed': processed})
                    record_worker_poll(worker_id)
                    if processed:
                        record_worker_result(worker_id, "openclaw_event", "processed", processed)
                time.sleep(1)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                import traceback
                logger.error(f"Event daemon iteration failed: {exc}\n{traceback.format_exc()}")
                logger.exception("openclaw event daemon iteration failed", extra={"worker_id": worker_id, "error": str(exc)})
                record_worker_result(worker_id, "openclaw_event", "failed", 1)
                time.sleep(max(settings.worker_poll_seconds, 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
