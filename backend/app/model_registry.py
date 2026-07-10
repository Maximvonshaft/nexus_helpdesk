from __future__ import annotations

"""Authoritative SQLAlchemy model registration for Alembic and drift gates.

Every ORM model family that contributes tables to ``Base.metadata`` belongs in
this registry.  Optional successor modules are imported only when they exist so
a base safety PR never references an unmerged implementation file.
"""

from importlib import import_module
from importlib.util import find_spec

REQUIRED_MODEL_MODULES: tuple[str, ...] = (
    "app.models",
    "app.webchat_models",
    "app.voice_models",
    "app.tool_models",
    "app.operator_models",
    "app.models_control_plane",
    "app.models_osr",
    "app.models_webchat_debug",
)

OPTIONAL_MODEL_MODULES: tuple[str, ...] = (
    "app.models_operations_dispatch",
)

REPRESENTATIVE_TABLES: dict[str, str] = {
    "app.models": "tickets",
    "app.webchat_models": "webchat_conversations",
    "app.voice_models": "webchat_voice_sessions",
    "app.tool_models": "tool_registry",
    "app.operator_models": "operator_tasks",
    "app.models_control_plane": "knowledge_items",
    "app.models_osr": "case_contexts",
    "app.models_webchat_debug": "webchat_ai_debug_runs",
    "app.models_operations_dispatch": "operations_dispatch_outbox",
}


def register_all_models() -> tuple[str, ...]:
    """Import all required model modules and any optional module that exists.

    Returning the imported module names gives tests and diagnostic scripts a
    stable, non-secret way to prove what metadata families were loaded.
    """

    imported: list[str] = []
    for module_name in REQUIRED_MODEL_MODULES:
        import_module(module_name)
        imported.append(module_name)
    for module_name in OPTIONAL_MODEL_MODULES:
        if find_spec(module_name) is None:
            continue
        import_module(module_name)
        imported.append(module_name)
    return tuple(imported)
