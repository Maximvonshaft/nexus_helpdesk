from __future__ import annotations

import logging

from ..db import db_context
from .background_jobs import enqueue_auto_reply_job


def fire_and_forget_auto_reply(ticket_id: int, user_id: int):
    try:
        with db_context() as db:
            enqueue_auto_reply_job(db, ticket_id=ticket_id, user_id=user_id)
    except Exception as exc:
        logging.error("Auto-reply enqueue failed: %s", exc)
