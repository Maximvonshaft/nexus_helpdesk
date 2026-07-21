from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

_CURRENT_AGENT_RELEASE: ContextVar[dict[str, Any] | None] = ContextVar(
    "nexus_current_agent_release",
    default=None,
)
_CURRENT_AGENT_TOOL_HANDLERS: ContextVar[dict[str, Any] | None] = ContextVar(
    "nexus_current_agent_tool_handlers",
    default=None,
)


@contextmanager
def bind_agent_release_snapshot(
    snapshot: dict[str, Any] | None,
) -> Iterator[None]:
    token = _CURRENT_AGENT_RELEASE.set(snapshot if isinstance(snapshot, dict) else None)
    try:
        yield
    finally:
        _CURRENT_AGENT_RELEASE.reset(token)


@contextmanager
def bind_agent_tool_handlers(
    handlers: dict[str, Any] | None,
) -> Iterator[None]:
    """Bind request-local Agent Tool handlers for the canonical executor.

    The handler map is contextual rather than global so concurrent Agent runs do
    not mutate module state or leak tenant/release resources across requests.
    """

    token = _CURRENT_AGENT_TOOL_HANDLERS.set(
        dict(handlers) if isinstance(handlers, dict) else None
    )
    try:
        yield
    finally:
        _CURRENT_AGENT_TOOL_HANDLERS.reset(token)


def current_agent_release_snapshot() -> dict[str, Any] | None:
    value = _CURRENT_AGENT_RELEASE.get()
    return value if isinstance(value, dict) else None


def current_agent_tool_handler(tool_name: str) -> Any | None:
    handlers = _CURRENT_AGENT_TOOL_HANDLERS.get()
    if not isinstance(handlers, dict):
        return None
    return handlers.get(str(tool_name or "").strip())


def released_knowledge_evidence() -> tuple[dict[str, Any], ...] | None:
    snapshot = current_agent_release_snapshot()
    if snapshot is None or snapshot.get("source") != "deployment":
        return None
    resolved = snapshot.get("resolved")
    rows = resolved.get("knowledge") if isinstance(resolved, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("agent_release_knowledge_evidence_invalid")
    output: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("agent_release_knowledge_reference_invalid")
        key = str(row.get("item_key") or "").strip().lower()
        try:
            version = int(row.get("version"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("agent_release_knowledge_version_invalid") from exc
        snapshot_json = row.get("snapshot")
        if not key or version <= 0 or not isinstance(snapshot_json, dict):
            raise RuntimeError("agent_release_knowledge_reference_invalid")
        output.append(
            {
                "id": int(row.get("id") or 0),
                "item_key": key,
                "version": version,
                "snapshot": snapshot_json,
            }
        )
    return tuple(output)


def released_knowledge_versions() -> frozenset[tuple[str, int]] | None:
    rows = released_knowledge_evidence()
    if rows is None:
        return None
    return frozenset((str(row["item_key"]), int(row["version"])) for row in rows)
