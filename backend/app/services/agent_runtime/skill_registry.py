from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..webchat_ai_decision_runtime.tool_registry import get_tool_contract

_SKILL_FILE = Path(__file__).resolve().parents[2] / "agent_skills" / "skills.json"


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    tools: tuple[str, ...]
    instructions: tuple[str, ...]

    def prompt_projection(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "tools": list(self.tools),
            "instructions": list(self.instructions),
        }


@lru_cache(maxsize=1)
def load_skills() -> tuple[SkillDefinition, ...]:
    raw = json.loads(_SKILL_FILE.read_text(encoding="utf-8"))
    if raw.get("schema_version") != "nexus.agent_skills.v1":
        raise RuntimeError("unsupported agent skill registry schema")
    skills: list[SkillDefinition] = []
    seen: set[str] = set()
    for item in raw.get("skills") or []:
        if not isinstance(item, dict):
            raise RuntimeError("agent skill entries must be objects")
        name = " ".join(str(item.get("name") or "").strip().split())
        description = " ".join(str(item.get("description") or "").strip().split())
        tools = tuple(str(value).strip() for value in item.get("tools") or [] if str(value).strip())
        instructions = tuple(
            " ".join(str(value or "").strip().split())
            for value in item.get("instructions") or []
            if str(value or "").strip()
        )
        if not name or not description or not instructions:
            raise RuntimeError("agent skill requires name, description and instructions")
        if name in seen:
            raise RuntimeError(f"duplicate agent skill: {name}")
        unknown_tools = [tool for tool in tools if get_tool_contract(tool) is None]
        if unknown_tools:
            raise RuntimeError(f"agent skill {name} references unknown tools: {unknown_tools}")
        seen.add(name)
        skills.append(SkillDefinition(name, description, tools, instructions))
    return tuple(skills)


def prompt_skill_catalog(*, available_tools: set[str] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for skill in load_skills():
        tools = tuple(tool for tool in skill.tools if available_tools is None or tool in available_tools)
        if skill.tools and not tools:
            continue
        row = skill.prompt_projection()
        row["tools"] = list(tools)
        rows.append(row)
    return rows


def all_skill_tool_names() -> tuple[str, ...]:
    return tuple(dict.fromkeys(tool for skill in load_skills() for tool in skill.tools))
