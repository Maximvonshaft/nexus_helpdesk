from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator

_ADMINISTRATOR_ISSUED_CREDENTIAL = ContextVar(
    "nexus_administrator_issued_credential",
    default=False,
)
_ADMINISTRATOR_ISSUED_TENANT_ID: ContextVar[int | None] = ContextVar(
    "nexus_administrator_issued_tenant_id",
    default=None,
)


def administrator_issued_credential_active() -> bool:
    """Return whether the current request is creating an administrator-issued password.

    The flag is request-scoped and intentionally separate from generic ORM
    insertion. Bootstrap, fixtures, imports and other internal user creation
    paths must not silently inherit an interactive first-login policy.
    """

    return bool(_ADMINISTRATOR_ISSUED_CREDENTIAL.get())


def administrator_issued_tenant_id() -> int | None:
    """Return the server-derived Tenant for the active admin user-create command."""

    return _ADMINISTRATOR_ISSUED_TENANT_ID.get()


@contextmanager
def administrator_issued_credential_scope(active: bool) -> Iterator[None]:
    token = _ADMINISTRATOR_ISSUED_CREDENTIAL.set(bool(active))
    try:
        yield
    finally:
        _ADMINISTRATOR_ISSUED_CREDENTIAL.reset(token)


@contextmanager
def administrator_issued_tenant_scope(tenant_id: int | None) -> Iterator[None]:
    token = _ADMINISTRATOR_ISSUED_TENANT_ID.set(tenant_id)
    try:
        yield
    finally:
        _ADMINISTRATOR_ISSUED_TENANT_ID.reset(token)
