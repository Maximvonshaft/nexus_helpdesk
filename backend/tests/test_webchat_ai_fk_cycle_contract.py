from __future__ import annotations

import warnings

from sqlalchemy import create_engine
from sqlalchemy.exc import SAWarning

from app.db import Base
from app.model_registry import register_all_models
from app.webchat_models import WebchatMessage


def _ai_turn_foreign_key():
    foreign_keys = list(WebchatMessage.__table__.c.ai_turn_id.foreign_keys)
    assert len(foreign_keys) == 1
    return foreign_keys[0]


def test_webchat_message_ai_turn_back_reference_is_named_and_deferred():
    foreign_key = _ai_turn_foreign_key()
    assert foreign_key.target_fullname == "webchat_ai_turns.id"
    assert foreign_key.constraint.name == "fk_webchat_messages_ai_turn_id"
    assert foreign_key.constraint.use_alter is True


def test_registered_metadata_create_drop_has_no_unsortable_cycle_warning():
    register_all_models()
    isolated = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", SAWarning)
        Base.metadata.create_all(bind=isolated)
        Base.metadata.drop_all(bind=isolated)
    cycle_warnings = [
        str(item.message)
        for item in captured
        if "unresolvable cycles" in str(item.message).lower()
        or "can't sort tables for drop" in str(item.message).lower()
    ]
    assert cycle_warnings == []
