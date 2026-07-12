from __future__ import annotations

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
