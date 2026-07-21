from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..agent_control_config import PLAYBOOK
from ..agent_tool_contracts import bootstrap_agent_tool_contracts

bootstrap_agent_tool_contracts()


@dataclass(frozen=True)
class PlaybookDefinition:
    resource_id: int
    resource_key: str
    name: str
    display_name: str
    description: str
    tools: tuple[str, ...]
    instructions: tuple[str, ...]
    priority: int
    published_version: int
    scope_rank: int

    def prompt_projection(
        self,
        *,
        available_tools: set[str] | None = None,
    ) -> dict[str, Any] | None:
        tools = tuple(
            name
            for name in self.tools
            if available_tools is None or name in available_tools
        )
        if self.tools and not tools:
            return None
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "tools": list(tools),
            "instructions": list(self.instructions),
            "resource_key": self.resource_key,
            "published_version": self.published_version,
        }


def load_playbooks(
    db: Session,
    *,
    market_id: int | None = None,
    channel: str | None = None,
    language: str | None = None,
    release_snapshot: dict[str, Any] | None = None,
) -> tuple[PlaybookDefinition, ...]:
    del db, market_id, channel, language
    rows = _released_playbooks(release_snapshot)
    rows.sort(key=lambda item: (-item.scope_rank, item.priority, item.resource_key))
    return tuple(rows)


def prompt_playbook_catalog(
    db: Session,
    *,
    market_id: int | None = None,
    channel: str | None = None,
    language: str | None = None,
    available_tools: set[str] | None = None,
    release_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for playbook in load_playbooks(
        db,
        market_id=market_id,
        channel=channel,
        language=language,
        release_snapshot=release_snapshot,
    ):
        projected = playbook.prompt_projection(available_tools=available_tools)
        if projected is not None:
            output.append(projected)
    return output


def all_playbook_tool_names(
    db: Session,
    *,
    market_id: int | None = None,
    channel: str | None = None,
    language: str | None = None,
    release_snapshot: dict[str, Any] | None = None,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            tool
            for playbook in load_playbooks(
                db,
                market_id=market_id,
                channel=channel,
                language=language,
                release_snapshot=release_snapshot,
            )
            for tool in playbook.tools
        )
    )


def _released_playbooks(
    release_snapshot: dict[str, Any] | None,
) -> list[PlaybookDefinition]:
    if not isinstance(release_snapshot, dict) or release_snapshot.get("source") != "deployment":
        raise RuntimeError("agent_release_snapshot_required_for_playbooks")
    resolved = release_snapshot.get("resolved")
    if not isinstance(resolved, dict):
        raise RuntimeError("agent_release_resolved_resources_missing")
    resources = resolved.get("resources")
    if not isinstance(resources, list):
        raise RuntimeError("agent_release_resources_invalid")
    rows: list[PlaybookDefinition] = []
    for item in resources:
        if not isinstance(item, dict) or item.get("config_type") != PLAYBOOK:
            continue
        content = item.get("content")
        if not isinstance(content, dict):
            raise RuntimeError("agent_release_playbook_content_invalid")
        rows.append(
            PlaybookDefinition(
                resource_id=int(item.get("id") or 0),
                resource_key=str(item.get("resource_key") or ""),
                name=str(content.get("name") or ""),
                display_name=str(content.get("display_name") or content.get("name") or ""),
                description=str(content.get("description") or ""),
                tools=tuple(str(name) for name in content.get("tools") or []),
                instructions=tuple(str(text) for text in content.get("instructions") or []),
                priority=int(content.get("priority") or 100),
                published_version=int(item.get("version") or 0),
                scope_rank=100,
            )
        )
    return rows
