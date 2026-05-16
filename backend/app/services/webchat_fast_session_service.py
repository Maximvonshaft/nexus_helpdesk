from __future__ import annotations

from typing import Any

FAST_ORIGIN = "webchat-fast"
FAST_CONTEXT_LIMIT = 10


def clean_fast_context(items: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "visitor").lower()
        if role in {"ai", "assistant", "agent", "bot"}:
            role = "agent"
        else:
            role = "visitor"
        text = str(item.get("text") or item.get("body") or item.get("content") or "").strip()
        if text:
            out.append({"role": role, "text": text[:500]})
    return out[-FAST_CONTEXT_LIMIT:]


def merge_fast_context(server_context: list[dict[str, Any]] | None, frontend_context: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in clean_fast_context(server_context) + clean_fast_context(frontend_context):
        key = (item["role"], item["text"])
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged[-FAST_CONTEXT_LIMIT:]
