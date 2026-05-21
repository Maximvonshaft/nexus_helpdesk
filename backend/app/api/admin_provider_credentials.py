from __future__ import annotations

from fastapi import APIRouter

# Provider credential administration is intentionally not mounted/exposed in this phase.
# The credential store, crypto, refresh manager, device-flow service, and adapters can
# remain under test, but admin mutation APIs must not be exposed until tenant-bound
# production authorization and UI flows are completed.

router = APIRouter(prefix="/api/admin/provider-credentials", tags=["admin-provider-credentials"])
