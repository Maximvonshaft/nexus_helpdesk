from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, event, inspect
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import User
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class UserSecurityState(Base):
    """Canonical mutable security state for one operator identity.

    Authentication credentials remain on ``users``. This table owns session
    revocation, forced password rotation and bounded login metadata so those
    concerns are not duplicated across authentication and administration APIs.
    """

    __tablename__ = "user_security_states"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    session_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


def _security_table_exists(connection: Connection) -> bool:
    return inspect(connection).has_table(UserSecurityState.__tablename__)


@event.listens_for(User, "after_insert")
def _create_security_state_for_new_user(mapper, connection: Connection, target: User) -> None:  # noqa: ANN001
    del mapper
    if not _security_table_exists(connection):
        return
    now = utc_now()
    table = UserSecurityState.__table__
    connection.execute(
        table.insert().values(
            user_id=target.id,
            session_version=1,
            must_change_password=True,
            password_changed_at=None,
            last_login_at=None,
            created_at=now,
            updated_at=now,
        )
    )


@event.listens_for(User, "after_update")
def _rotate_security_state_after_identity_change(mapper, connection: Connection, target: User) -> None:  # noqa: ANN001
    del mapper
    if not _security_table_exists(connection):
        return

    inspected = inspect(target)
    password_changed = inspected.attrs.password_hash.history.has_changes()
    active_history = inspected.attrs.is_active.history
    deactivated = active_history.has_changes() and target.is_active is False
    if not password_changed and not deactivated:
        return

    now = utc_now()
    table = UserSecurityState.__table__
    values = {
        "session_version": table.c.session_version + 1,
        "updated_at": now,
    }
    if password_changed:
        values.update(
            must_change_password=True,
            password_changed_at=now,
        )

    result = connection.execute(
        table.update()
        .where(table.c.user_id == target.id)
        .values(**values)
    )
    if result.rowcount == 0:
        connection.execute(
            table.insert().values(
                user_id=target.id,
                session_version=2,
                must_change_password=password_changed,
                password_changed_at=now if password_changed else None,
                last_login_at=None,
                created_at=now,
                updated_at=now,
            )
        )
