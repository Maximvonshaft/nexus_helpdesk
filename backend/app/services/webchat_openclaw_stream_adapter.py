from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class ContentDelta:
    text: str


@dataclass(frozen=True)
class Completed:
    full_payload: dict[str, Any] | None = None
    full_text: str | None = None


@dataclass(frozen=True)
class ToolCallDetected:
    raw_type: str | None = None


@dataclass(frozen=True)
class StreamError:
    error_code: str
    message: str | None = None

NormalizedStreamEvent = ContentDelta | Completed | ToolCallDetected | StreamError

_TOOL_HINTS = ("tool", "function_call", "function.call")
_IGNORE_TYPES = {"response.created", "response.in_progress", "response.output_item.done", "response.content_part.added"}


def _event_type(payload: dict[str, Any], event_name: str | None = None) -> str:
    return str(payload.get("type") or payload.get("event") or event_name or "").strip()


def _delta_text(payload: dict[str, Any]) -> str | None:
    for key in ("delta", "text", "output_text"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    delta = payload.get("delta")
    if isinstance(delta, dict):
        value = delta.get("text") or delta.get("value")
        if isinstance(value, str):
            return value
    return None


def _completed_text(payload: dict[str, Any]) -> str | None:
    for key in ("output_text", "text", "response_text"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    response = payload.get("response")
    if isinstance(response, dict):
        for key in ("output_text", "text"):
            value = response.get(key)
            if isinstance(value, str):
                return value
        output = response.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    value = part.get("text") or part.get("output_text")
                    if isinstance(value, str):
                        return value
    return None


def _item_type(payload: dict[str, Any]) -> str:
    item = payload.get("item")
    if isinstance(item, dict):
        return str(item.get("type") or "").strip().lower()
    return ""


class OpenClawResponsesStreamAdapter:
    """Normalize OpenClaw SSE/JSONL transport into safe internal events.

    Raw provider lines terminate here. Browser API layers must only consume the
    normalized event classes defined above.
    """

    def feed_json_payload(self, payload: dict[str, Any], *, event_name: str | None = None) -> list[NormalizedStreamEvent]:
        event_type = _event_type(payload, event_name)
        lower_type = event_type.lower()
        item_type = _item_type(payload)
        if any(hint in lower_type for hint in _TOOL_HINTS):
            return [ToolCallDetected(raw_type=event_type or None)]
        if event_type == "response.output_item.added" and item_type in {"function_call", "tool_call", "tool_result", "function"}:
            return [ToolCallDetected(raw_type=event_type or None)]
        if event_type in _IGNORE_TYPES or event_type == "response.output_item.added":
            return []
        if event_type == "response.output_text.delta":
            text = _delta_text(payload)
            return [ContentDelta(text)] if text else []
        if event_type == "response.output_text.done":
            return [Completed(full_payload=payload, full_text=_completed_text(payload))]
        if event_type in {"response.completed", "response.done", "done"}:
            return [Completed(full_payload=payload, full_text=_completed_text(payload))]
        if event_type in {"response.error", "error"}:
            error = payload.get("error")
            message = None
            if isinstance(error, dict):
                message = str(error.get("message") or "") or None
            return [StreamError(error_code="openclaw_stream_error", message=message)]
        if event_type:
            return []
        return [StreamError(error_code="openclaw_malformed_stream", message="missing stream event type")]

    def feed_json_line(self, line: str) -> list[NormalizedStreamEvent]:
        text = line.strip()
        if not text:
            return []
        if text == "[DONE]":
            return []
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return [StreamError(error_code="openclaw_malformed_json", message="malformed JSONL stream line")]
        if not isinstance(payload, dict):
            return [StreamError(error_code="openclaw_malformed_json", message="JSONL stream line must be an object")]
        return self.feed_json_payload(payload)

    def feed_sse_block(self, block: str) -> list[NormalizedStreamEvent]:
        event_name: str | None = None
        data_lines: list[str] = []
        for raw_line in block.splitlines():
            line = raw_line.rstrip("\r")
            if not line or line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].lstrip())
        if not data_lines:
            return []
        data = "\n".join(data_lines)
        if data.strip() == "[DONE]":
            return []
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return [StreamError(error_code="openclaw_malformed_json", message="malformed SSE data JSON")]
        if not isinstance(payload, dict):
            return [StreamError(error_code="openclaw_malformed_json", message="SSE data must be an object")]
        return self.feed_json_payload(payload, event_name=event_name)

    def feed_raw_chunks(self, chunks: Iterable[str]) -> list[NormalizedStreamEvent]:
        events: list[NormalizedStreamEvent] = []
        buffer = ""
        for chunk in chunks:
            buffer += chunk
            while "\n\n" in buffer:
                block, buffer = buffer.split("\n\n", 1)
                if block.lstrip().startswith(("event:", "data:")):
                    events.extend(self.feed_sse_block(block))
                else:
                    for line in block.splitlines():
                        events.extend(self.feed_json_line(line))
        if buffer.strip():
            if buffer.lstrip().startswith(("event:", "data:")):
                events.extend(self.feed_sse_block(buffer))
            else:
                for line in buffer.splitlines():
                    events.extend(self.feed_json_line(line))
        return events
