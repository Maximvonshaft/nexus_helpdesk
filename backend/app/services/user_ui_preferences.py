from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models_user_ui_preferences import SUPPORTED_UI_LOCALES, UserUIPreference
from ..utils.time import utc_now

DEFAULT_UI_LOCALE = "zh-CN"


@dataclass(frozen=True)
class UserUILocaleState:
    ui_locale: str
    configured: bool


def normalize_ui_locale(value: str | None) -> str:
    candidate = str(value or "").strip().replace("_", "-")
    aliases = {
        "zh": "zh-CN",
        "zh-cn": "zh-CN",
        "zh-hans": "zh-CN",
        "en-gb": "en",
        "en-us": "en",
        "de-de": "de",
        "de-ch": "de",
        "de-at": "de",
    }
    normalized = aliases.get(candidate.lower(), candidate)
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
    row = db.get(UserUIPreference, int(user_id))
    previous = row.ui_locale if row is not None else DEFAULT_UI_LOCALE
    now = utc_now()
    if row is None:
        row = UserUIPreference(
            user_id=int(user_id),
            ui_locale=normalized,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.ui_locale = normalized
        row.updated_at = now
    db.flush()
    return previous, normalized
