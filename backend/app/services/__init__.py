from __future__ import annotations

try:
    from .openclaw_unresolved_store import apply_openclaw_unresolved_store_patch

    apply_openclaw_unresolved_store_patch()
except Exception:
    # Service package import must remain resilient. Detailed failures are covered
    # by the OpenClaw unresolved idempotency tests and runtime CI gates.
    pass
