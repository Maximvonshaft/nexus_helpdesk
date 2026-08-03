from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import User
from ..models_user_ui_preferences import SUPPORTED_UI_LOCALES, UserUIPreference
from ..utils.time import utc_now
from .audit_service import log_admin_audit

DEFAULT_UI_LOCALE = "zh-CN"


@dataclass(frozen=True)
class UserUILocaleState:
    ui_locale: str
    configured: bool


def normalize_ui_locale(value: str | None) -> str:
    candidate = str(value or "").strip().replace("_", "-").lower()
    aliases = {
        "zh": "zh-CN",
        "zh-cn": "zh-CN",
        "zh-hans": "zh-CN",
        "en": "en",
        "en-gb": "en",
        "en-us": "en",
        "de": "de",
        "de-de": "de",
        "de-ch": "de",
        "de-at": "de",
        "cnr": "cnr",
        "cnr-me": "cnr",
        "sr-me": "cnr",
        "sr-latn-me": "cnr",
    }
    normalized = aliases.get(candidate)
    if normalized not in SUPPORTED_UI_LOCALES:
        raise ValueError("ui_locale_unsupported")
    return normalized


def read_user_ui_locale_state(db: Session, user_id: int) -> UserUILocaleState:
    row = db.get(UserUIPreference, int(user_id))
    if row is None:
        return UserUILocaleState(ui_locale=DEFAULT_UI_LOCALE, configured=False)
    return UserUILocaleState(ui_locale=row.ui_locale, configured=True)


def read_user_ui_locale(db: Session, user_id: int) -> str:
    return read_user_ui_locale_state(db, user_id).ui_locale


def set_user_ui_locale(
    db: Session,
    *,
    user_id: int,
    ui_locale: str,
) -> tuple[str, str]:
    normalized = normalize_ui_locale(ui_locale)
    normalized_user_id = int(user_id)

    # Lock the stable parent row so simultaneous first writes from multiple
    # devices cannot race into duplicate preference inserts on PostgreSQL.
    db.execute(
        select(User.id)
        .where(User.id == normalized_user_id)
        .with_for_update(),
    ).scalar_one()

    row = db.get(UserUIPreference, normalized_user_id)
    was_configured = row is not None
    previous = row.ui_locale if row is not None else DEFAULT_UI_LOCALE

    if row is not None and row.ui_locale == normalized:
        return previous, normalized

    now = utc_now()
    if row is None:
        row = UserUIPreference(
            user_id=normalized_user_id,
            ui_locale=normalized,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.ui_locale = normalized
        row.updated_at = now
    db.flush()

    # A first explicit selection of the default value changes authority even
    # though the locale string itself stays zh-CN. The API layer records all
    # value changes; this branch records the otherwise invisible state change.
    if not was_configured and previous == normalized:
        log_admin_audit(
            db,
            actor_id=normalized_user_id,
            action="auth.ui_locale_changed",
            target_type="user_ui_preference",
            target_id=normalized_user_id,
            old_value={"ui_locale": previous, "configured": False},
            new_value={"ui_locale": normalized, "configured": True},
        )
        db.flush()

    return previous, normalized
