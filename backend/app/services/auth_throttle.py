from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from datetime import timedelta

from ..models import AuthThrottleEntry
from ..settings import get_settings
from ..utils.time import ensure_utc, utc_now

settings = get_settings()


def build_login_throttle_key(username: str, remote_addr: str | None) -> str:
    return f"{(username or '').strip().lower()}|{(remote_addr or 'unknown').strip()}"


def enforce_login_allowed(db: Session, throttle_key: str) -> None:
    entry = db.query(AuthThrottleEntry).filter(AuthThrottleEntry.throttle_key == throttle_key).first()
    if not entry:
        return
    now = utc_now()
    locked_until = ensure_utc(entry.locked_until)
    if locked_until and locked_until > now:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many failed login attempts. Try again later.")


def record_login_failure(db: Session, throttle_key: str) -> None:
    entry = db.query(AuthThrottleEntry).filter(AuthThrottleEntry.throttle_key == throttle_key).first()
    if not entry:
        entry = AuthThrottleEntry(throttle_key=throttle_key, fail_count=0)
        db.add(entry)
        db.flush()
    entry.fail_count += 1
    entry.last_failed_at = utc_now()
    if entry.fail_count >= settings.login_max_failures:
        entry.locked_until = utc_now() + timedelta(minutes=settings.login_lock_minutes)
    db.flush()


def clear_login_failures(db: Session, throttle_key: str) -> None:
    entry = db.query(AuthThrottleEntry).filter(AuthThrottleEntry.throttle_key == throttle_key).first()
    if entry:
        db.delete(entry)
        db.flush()
