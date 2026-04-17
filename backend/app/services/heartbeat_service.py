from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import ServiceHeartbeat
from ..utils.time import utc_now


def update_service_heartbeat(
    db: Session,
    *,
    service_name: str,
    instance_id: str | None,
    status: str = "ok",
    details: dict | None = None,
) -> ServiceHeartbeat:
    row = db.query(ServiceHeartbeat).filter(ServiceHeartbeat.service_name == service_name).first()
    if row is None:
        row = ServiceHeartbeat(service_name=service_name, instance_id=instance_id, status=status, details_json=details, last_seen_at=utc_now())
        db.add(row)
        db.flush()
    else:
        row.instance_id = instance_id
        row.status = status
        row.details_json = details
        row.last_seen_at = utc_now()
    row.updated_at = utc_now()
    return row
