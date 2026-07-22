"""retire standalone auto-reply jobs

Revision ID: 20260722_0074
Revises: 20260721_0073
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260722_0074"
down_revision = "20260721_0073"
branch_labels = None
depends_on = None

_RETIRED_JOB_TYPE = "auto_reply.send_update"
_RETIRED_PAYLOAD = (
    '{"job_type":"auto_reply.send_update","retired":true,'
    '"retirement_reason":"canonical_agent_runtime_only"}'
)


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE background_jobs
            SET status = 'dead',
                payload_json = :payload,
                locked_at = NULL,
                locked_by = NULL,
                next_run_at = NULL,
                last_error = 'retired: canonical Agent Runtime is the only AI execution authority',
                updated_at = CURRENT_TIMESTAMP
            WHERE job_type = :job_type
              AND status IN ('pending', 'processing')
            """
        ).bindparams(
            job_type=_RETIRED_JOB_TYPE,
            payload=_RETIRED_PAYLOAD,
        )
    )


def downgrade() -> None:
    # The removed executable and its model/provider semantics are intentionally
    # not resurrected. Restoring them would recreate a second AI authority.
    pass
