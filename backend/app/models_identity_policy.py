from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, event, inspect
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, Session, mapped_column, object_session
from sqlalchemy.orm.util import identity_key

from .db import Base
from .models import User
from .services.credential_creation_context import administrator_issued_credential_active
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)
_EXPIRE_POLICY_IDS = "nexus_credential_policy_expire_ids"


class UserCredentialPolicy(Base):
    """Credential lifecycle policy for one canonical User identity.

    Token freshness remains owned exclusively by ``User.updated_at`` and the
    capability fingerprint. This row stores only password-rotation policy and
    bounded account metadata; it is not a session store.
    """

    __tablename__ = "user_credential_policies"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )
    password_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


def _policy_table_exists(connection: Connection) -> bool:
    return inspect(connection).has_table(UserCredentialPolicy.__tablename__)


def _mark_policy_for_expiration(target: User) -> None:
    session = object_session(target)
    if session is not None:
        session.info.setdefault(_EXPIRE_POLICY_IDS, set()).add(target.id)


@event.listens_for(Session, "after_flush_postexec")
def _expire_changed_credential_policies(session: Session, flush_context) -> None:  # noqa: ANN001
    del flush_context
    for user_id in session.info.pop(_EXPIRE_POLICY_IDS, set()):
        row = session.identity_map.get(identity_key(UserCredentialPolicy, (user_id,)))
        if row is not None:
            session.expire(row)


@event.listens_for(User, "after_insert")
def _create_policy_for_new_user(mapper, connection: Connection, target: User) -> None:  # noqa: ANN001
    del mapper
    if not _policy_table_exists(connection):
        return
    now = utc_now()
    connection.execute(
        UserCredentialPolicy.__table__.insert().values(
            user_id=target.id,
            must_change_password=administrator_issued_credential_active(),
            password_changed_at=None,
            last_login_at=None,
            created_at=now,
            updated_at=now,
        )
    )


@event.listens_for(User, "after_update")
def _require_rotation_after_admin_password_write(mapper, connection: Connection, target: User) -> None:  # noqa: ANN001
    del mapper
    if not _policy_table_exists(connection):
        return
    if not inspect(target).attrs.password_hash.history.has_changes():
        return

    now = utc_now()
    table = UserCredentialPolicy.__table__
    result = connection.execute(
        table.update()
        .where(table.c.user_id == target.id)
        .values(
            must_change_password=True,
            password_changed_at=now,
            updated_at=now,
        )
    )
    if result.rowcount == 0:
        connection.execute(
            table.insert().values(
                user_id=target.id,
                must_change_password=True,
                password_changed_at=now,
                last_login_at=None,
                created_at=now,
                updated_at=now,
            )
        )
    _mark_policy_for_expiration(target)
