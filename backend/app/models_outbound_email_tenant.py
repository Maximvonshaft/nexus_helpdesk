from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import OutboundEmailAccount
from .services.outbound_email_tenant_context import current_outbound_email_tenant
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class OutboundEmailAccountTenantBinding(Base):
    """Sole tenant-ownership authority for an outbound email account.

    ``OutboundEmailAccount.market_id`` remains a delivery-routing scope. This
    binding owns tenant visibility and prevents a global route from becoming a
    cross-tenant account. It does not duplicate SMTP/IMAP account state.
    """

    __tablename__ = "outbound_email_account_tenant_bindings"

    account_id: Mapped[int] = mapped_column(
        ForeignKey("outbound_email_accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    assignment_source: Mapped[str] = mapped_column(String(40), nullable=False)
    assignment_version: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


@event.listens_for(OutboundEmailAccount, "after_insert")
def _bind_admin_created_email_account(mapper, connection: Connection, target: OutboundEmailAccount) -> None:  # noqa: ANN001
    del mapper
    scoped, tenant_id = current_outbound_email_tenant()
    if not scoped:
        return
    now = utc_now()
    connection.execute(
        OutboundEmailAccountTenantBinding.__table__.insert().values(
            account_id=target.id,
            tenant_id=tenant_id,
            assignment_source="runtime_principal" if tenant_id is not None else "legacy_shadow",
            assignment_version="nexus.outbound_email.tenant_authority.v1",
            created_at=now,
            updated_at=now,
        )
    )
