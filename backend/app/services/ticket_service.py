"""Compatibility import for the canonical Ticket service.

All production routes and new code must import ``canonical_ticket_service``.
The retained implementation core is private to that authority.
"""

from .canonical_ticket_service import *  # noqa: F401,F403
