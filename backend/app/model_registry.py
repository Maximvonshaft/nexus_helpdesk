from __future__ import annotations

"""Authoritative SQLAlchemy model registration for runtime, Alembic and drift gates.

Every merged ORM model family that contributes production tables to
``Base.metadata`` is required. Optional model loading is allowed only through an
explicit, capability-named plugin declaration; an enabled plugin is imported
fail-closed and must declare one representative table.
"""
from dataclasses import dataclass
from importlib import import_module


class ModelRegistryError(RuntimeError):
    """Raised when model registration is incomplete, ambiguous or unavailable."""


@dataclass(frozen=True)
class ModelPlugin:
    """Explicit optional model capability.

    Disabled plugins are not part of the runtime metadata contract. Enabling a
    plugin makes both its module and representative table mandatory; missing
    code never degrades into a silent no-op.
    """

    capability: str
    module_name: str
    representative_table: str
    enabled: bool = False


REQUIRED_MODEL_MODULES: tuple[str, ...] = (
    "app.models",
    "app.models_identity_policy",
    "app.models_outbound_email_tenant",
    "app.webchat_models",
    "app.models_webchat_binding",
    "app.voice_models",
    "app.tool_models",
    "app.operator_models",
    "app.models_control_plane",
    "app.models_osr",
    "app.models_webchat_debug",
    "app.models_operations_dispatch",
)

# There are currently no optional production model plugins. Future plugins must
# be declared here with an explicit capability and enabled state; do not restore
# file-existence-based discovery.
MODEL_PLUGINS: tuple[ModelPlugin, ...] = ()

REPRESENTATIVE_TABLES: dict[str, str] = {
    "app.models": "tickets",
    "app.models_identity_policy": "user_credential_policies",
    "app.models_outbound_email_tenant": "outbound_email_account_tenant_bindings",
    "app.webchat_models": "webchat_conversations",
    "app.models_webchat_binding": "webchat_public_origin_bindings",
    "app.voice_models": "webchat_voice_sessions",
    "app.tool_models": "tool_registry",
    "app.operator_models": "operator_tasks",
    "app.models_control_plane": "knowledge_items",
    "app.models_osr": "case_contexts",
    "app.models_webchat_debug": "webchat_ai_debug_runs",
    "app.models_operations_dispatch": "operations_dispatch_outbox",
}


def declared_model_modules() -> tuple[str, ...]:
    """Return the exact model-module contract for this runtime."""

    return REQUIRED_MODEL_MODULES + tuple(
        plugin.module_name for plugin in MODEL_PLUGINS if plugin.enabled
    )


def validate_model_registry() -> tuple[str, ...]:
    """Validate registry structure before importing any model family."""

    modules = declared_model_modules()
    errors: list[str] = []

    duplicate_modules = sorted({name for name in modules if modules.count(name) > 1})
    if duplicate_modules:
        errors.append("duplicate model modules: " + ", ".join(duplicate_modules))

    declared = set(modules)
    representative_modules = set(REPRESENTATIVE_TABLES)
    missing_representatives = sorted(declared - representative_modules)
    stale_representatives = sorted(representative_modules - declared)
    if missing_representatives:
        errors.append(
            "missing representative tables for: " + ", ".join(missing_representatives)
        )
    if stale_representatives:
        errors.append(
            "representative tables declared for inactive modules: "
            + ", ".join(stale_representatives)
        )

    representative_values = list(REPRESENTATIVE_TABLES.values())
    duplicate_tables = sorted(
        {name for name in representative_values if representative_values.count(name) > 1}
    )
    if duplicate_tables:
        errors.append("duplicate representative tables: " + ", ".join(duplicate_tables))

    plugin_capabilities: set[str] = set()
    for plugin in MODEL_PLUGINS:
        capability = plugin.capability.strip()
        if not capability:
            errors.append(f"plugin {plugin.module_name!r} has no capability")
        elif capability in plugin_capabilities:
            errors.append(f"duplicate plugin capability: {capability}")
        plugin_capabilities.add(capability)
        if plugin.enabled:
            configured_table = REPRESENTATIVE_TABLES.get(plugin.module_name)
            if configured_table != plugin.representative_table:
                errors.append(
                    f"enabled plugin {plugin.module_name!r} representative table mismatch"
                )

    if errors:
        raise ModelRegistryError("invalid model registry: " + "; ".join(errors))
    return modules


def register_all_models() -> tuple[str, ...]:
    """Import every required model family and every explicitly enabled plugin."""

    modules = validate_model_registry()
    imported: list[str] = []
    for module_name in modules:
        try:
            import_module(module_name)
        except ModuleNotFoundError as exc:
            missing_name = exc.name or "unknown"
            raise ModelRegistryError(
                f"failed to import required model module {module_name!r}; "
                f"missing module or dependency {missing_name!r}"
            ) from exc
        imported.append(module_name)
    return tuple(imported)
