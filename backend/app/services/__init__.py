from __future__ import annotations

"""Nexus service package.

Importing services must not mutate public functions in other modules. Worker queue
entrypoints select their canonical transaction boundaries explicitly.
"""
