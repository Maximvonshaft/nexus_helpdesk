from __future__ import annotations

try:
    from .openclaw_unresolved_store import apply_openclaw_unresolved_store_patch

    apply_openclaw_unresolved_store_patch()
except Exception:
    # Service package import must remain resilient. Detailed failures are covered
    # by the OpenClaw unresolved idempotency tests and runtime CI gates.
    pass

try:
    from .openclaw_p0_runtime_security import apply_openclaw_p0_runtime_security_patch

    apply_openclaw_p0_runtime_security_patch()
except Exception:
    # Runtime hardening must never prevent the service package from importing.
    # Dedicated P0 regression tests cover the expected live rebinding behavior.
    pass

try:
    from .outbound_dispatch_transaction_boundary import apply_outbound_dispatch_transaction_boundary_patch

    apply_outbound_dispatch_transaction_boundary_patch()
except Exception:
    # Preserve service import resilience; transaction-boundary behavior is locked
    # by outbound dispatch regression tests and CI.
    pass
