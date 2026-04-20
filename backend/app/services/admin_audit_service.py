from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..utils.time import utc_now


def log_admin_action(
    db: Session,
    *,
    actor_id: int | None,
    action: str,
    target_type: str,
    target_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    db.execute(
        text(
            """
            INSERT INTO admin_audit_logs (
                actor_id,
                action,
                target_type,
                target_id,
                detail_json,
                created_at
            ) VALUES (
                :actor_id,
                :action,
                :target_type,
                :target_id,
                :detail_json,
                :created_at
            )
            """
        ),
        {
            "actor_id": actor_id,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "detail_json": json.dumps(detail or {}, ensure_ascii=False),
            "created_at": utc_now(),
        },
    )
