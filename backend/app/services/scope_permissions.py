"""Compatibility exports for capability-derived visibility helpers.

The canonical implementation lives in ``permissions.py`` so policy resolution,
role defaults, explicit overrides, and visibility semantics cannot drift.
"""

from .permissions import has_global_admin_visibility, has_global_case_visibility

__all__ = ["has_global_admin_visibility", "has_global_case_visibility"]
