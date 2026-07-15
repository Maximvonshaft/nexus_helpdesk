"""Compatibility import for the canonical Control Tower service.

No business, authorization, scope or route logic may be added here.
"""

from .canonical_control_tower_service import build_control_tower, submit_control_tower_action

__all__ = ["build_control_tower", "submit_control_tower_action"]
