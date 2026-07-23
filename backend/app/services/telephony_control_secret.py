from __future__ import annotations

import os
from pathlib import Path

from ..settings import get_settings

_INLINE_ENV = "TELEPHONY_CONTROL_SECRET"
_FILE_ENV = "TELEPHONY_CONTROL_SECRET_FILE"


def load_telephony_control_secret() -> str:
    """Load the one bounded encryption authority shared by Web and Telephony Worker.

    This secret protects durable Voice Commands and Provider Event replay envelopes.
    It is intentionally independent from the Web JWT secret so the background
    worker never receives authentication credentials.
    """

    inline = (os.getenv(_INLINE_ENV) or "").strip()
    file_path = (os.getenv(_FILE_ENV) or "").strip()
    if inline and file_path:
        raise RuntimeError("telephony_control_secret_authority_ambiguous")
    if inline:
        value = inline
    elif file_path:
        try:
            value = Path(file_path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError("telephony_control_secret_file_unreadable") from exc
    else:
        value = ""

    settings = get_settings()
    if not value:
        if settings.app_env == "production":
            raise RuntimeError("telephony_control_secret_required")
        return "development-only-telephony-control-secret"
    if len(value) < 32:
        raise RuntimeError("telephony_control_secret_too_short")
    return value
