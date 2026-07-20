"""Canonical backend test-process environment.

The production deployment authority remains ``enforce``.  The backend regression
suite deliberately runs in ``shadow`` mode so tests exercise tenant projections
without depending on an ambient runner or developer-shell variable.
"""

from __future__ import annotations

import os


os.environ.setdefault("TENANT_RUNTIME_AUTHORITY_MODE", "shadow")
