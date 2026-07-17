from __future__ import annotations

from .background_job_transaction_boundary import (
    apply_background_job_transaction_boundary_patch,
)
from .outbound_dispatch_transaction_boundary import (
    apply_outbound_dispatch_transaction_boundary_patch,
)

# These boundaries are part of the canonical worker runtime contract. Import or
# installation failure must stop startup rather than silently running an unsafe
# batch implementation without per-attempt rollback and crash recovery.
apply_outbound_dispatch_transaction_boundary_patch()
apply_background_job_transaction_boundary_patch()
