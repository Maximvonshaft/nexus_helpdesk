from __future__ import annotations

# Import side effect: registers WebchatFastIdempotency on app.db.Base.metadata
# for Alembic metadata discovery and test table creation.
from .services.webchat_fast_idempotency_db import WebchatFastIdempotency

__all__ = ["WebchatFastIdempotency"]
