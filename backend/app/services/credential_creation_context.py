from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator

_ADMINISTRATOR_ISSUED_CREDENTIAL = ContextVar(
    "nexus_administrator_issued_credential",
    default=False,
)


def administrator_issued_credential_active() -> bool:
    """Return whether the current request is creating an administrator-issued password.

    The flag is request-scoped and intentionally separate from generic ORM
    insertion. Bootstrap, fixtures, imports and other internal user creation
    paths must not silently inherit an interactive first-login policy.
    """

    return bool(_ADMINISTRATOR_ISSUED_CREDENTIAL.get())


@contextmanager
def administrator_issued_credential_scope(active: bool) -> Iterator[None]:
    token = _ADMINISTRATOR_ISSUED_CREDENTIAL.set(bool(active))
    try:
        yield
    finally:
        _ADMINISTRATOR_ISSUED_CREDENTIAL.reset(token)
