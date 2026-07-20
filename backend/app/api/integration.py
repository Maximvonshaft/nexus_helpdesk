"""Backward-compatible import path for the integration API.

The canonical implementation lives in :mod:`integration_runtime`.  Keeping this
thin module preserves the public Python import contract for integrations and
for operational test tooling without registering a duplicate FastAPI router.
"""

from .integration_runtime import *  # noqa: F403

