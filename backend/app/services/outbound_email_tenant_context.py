from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator

_UNSCOPED = object()
_OUTBOUND_EMAIL_TENANT: ContextVar[int | None | object] = ContextVar(
    "nexus_outbound_email_tenant",
    default=_UNSCOPED,
)


def current_outbound_email_tenant() -> tuple[bool, int | None]:
    value = _OUTBOUND_EMAIL_TENANT.get()
    if value is _UNSCOPED:
        return False, None
    return True, value if isinstance(value, int) else None


@contextmanager
def outbound_email_tenant_scope(tenant_id: int | None) -> Iterator[None]:
    token = _OUTBOUND_EMAIL_TENANT.set(tenant_id)
    try:
        yield
    finally:
        _OUTBOUND_EMAIL_TENANT.reset(token)
