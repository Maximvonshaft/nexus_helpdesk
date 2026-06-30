from __future__ import annotations

try:
    from .openclaw_unresolved_store import apply_openclaw_unresolved_store_patch

    apply_openclaw_unresolved_store_patch()
except Exception:
    # Service package import must remain resilient. Detailed failures are covered
    # by the OpenClaw unresolved idempotency tests and runtime CI gates.
    pass

try:
    from .outbound_dispatch_transaction_boundary import apply_outbound_dispatch_transaction_boundary_patch

    apply_outbound_dispatch_transaction_boundary_patch()
except Exception:
    # Preserve service import resilience; transaction-boundary behavior is locked
    # by outbound dispatch regression tests and CI.
    pass

try:
    from .background_job_transaction_boundary import apply_background_job_transaction_boundary_patch

    apply_background_job_transaction_boundary_patch()
except Exception:
    # Preserve service import resilience; background job attempt isolation is
    # locked by background job transaction-boundary regression tests and CI.
    pass
