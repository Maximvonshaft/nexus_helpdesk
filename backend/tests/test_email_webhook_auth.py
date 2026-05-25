from __future__ import annotations

import hashlib
import hmac
import time

import pytest
from fastapi import HTTPException

from app.api.email_integrations import _verify_hmac
from app.settings import get_settings
from email_test_utils import make_session


def test_webhook_hmac_accepts_once_and_rejects_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("EMAIL_WEBHOOK_SECRET", "secret")
    get_settings.cache_clear()
    engine, db = make_session(tmp_path)
    try:
        body = b'{"ok":true}'
        ts = str(int(time.time()))
        sig = hmac.new(b"secret", ts.encode() + b"." + body, hashlib.sha256).hexdigest()
        _verify_hmac(db, body=body, timestamp_header=ts, signature_header=f"sha256={sig}")
        db.flush()
        with pytest.raises(HTTPException) as exc:
            _verify_hmac(db, body=body, timestamp_header=ts, signature_header=f"sha256={sig}")
        assert exc.value.status_code == 409
    finally:
        db.close()
        engine.dispose()
