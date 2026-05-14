from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .webchat_fast_output_parser import FastReplyParseError, ParsedFastReply, parse_openclaw_fast_reply
from .webchat_openclaw_stream_adapter import ContentDelta, Completed, ToolCallDetected, StreamError

FORBIDDEN_PHRASES = [
    "OpenClaw",
    "gateway",
    "prompt",
    "system prompt",
    "developer message",
    "token",
    "localhost",
    "127.0.0.1",
    "port",
    "Authorization",
    "Bearer",
]
HOLD_BACK_CHARS = max(len(item) for item in FORBIDDEN_PHRASES) - 1


class StreamingReplyAbort(RuntimeError):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


@dataclass(frozen=True)
class ReplyDelta:
    text: str


import re

_FORBIDDEN_PATTERNS = [
    re.compile(r"(?<![a-zA-Z])" + re.escape(phrase) + r"(?![a-zA-Z])", re.IGNORECASE)
    for phrase in FORBIDDEN_PHRASES
]

def _has_forbidden(value: str) -> bool:
    normalized = " ".join(value.split())
    for pattern in _FORBIDDEN_PATTERNS:
        if pattern.search(normalized):
            return True
    return False


class StreamingReplyExtractor:
    """Extract only the JSON string value of key `reply` from streamed text.

    The extractor is intentionally not regex-based. It tracks JSON object keys,
    string boundaries, escapes, and unicode escapes so customer-visible deltas
    never contain JSON braces, keys, commas, intent, tracking fields, or handoff
    metadata.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._emitted = ""
        self._holdback = ""
        self._aborted = False

    @property
    def emitted_text(self) -> str:
        return self._emitted

    def _decode_reply_prefix(self) -> str:
        text = self._buffer.lstrip()
        if text.startswith("```"):
            raise StreamingReplyAbort("ai_invalid_output")
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            # Incomplete JSON while streaming is normal. Try a small state-machine
            # extraction so deltas flush before Completed.
            return self._extract_reply_from_incomplete_json(text)
        if not isinstance(value, dict):
            raise StreamingReplyAbort("ai_invalid_output")
        reply = value.get("reply")
        if not isinstance(reply, str) or not reply.strip():
            raise StreamingReplyAbort("ai_invalid_output")
        return reply

    def _extract_reply_from_incomplete_json(self, text: str) -> str:
        i = 0
        n = len(text)
        in_string = False
        escape = False
        unicode_left = 0
        token = ""
        current_key: str | None = None
        expecting_key = True
        expecting_colon = False
        expecting_value_for: str | None = None
        reply_chars: list[str] = []
        reading_reply = False
        while i < n:
            ch = text[i]
            if not in_string:
                if ch == '"':
                    in_string = True
                    escape = False
                    unicode_left = 0
                    token = ""
                    reading_reply = expecting_value_for == "reply"
                elif ch == ":" and expecting_colon:
                    expecting_value_for = current_key
                    expecting_colon = False
                elif ch == ",":
                    expecting_key = True
                    expecting_value_for = None
                    current_key = None
                elif ch in "{[" or ch.isspace():
                    pass
                elif expecting_value_for == "reply":
                    # reply must be a JSON string value.
                    raise StreamingReplyAbort("ai_invalid_output")
                i += 1
                continue

            if unicode_left:
                token += ch
                if reading_reply:
                    # Delay unicode decoding to json.loads on a synthetic string.
                    pass
                unicode_left -= 1
                i += 1
                continue
            if escape:
                token += "\\" + ch
                if ch == "u":
                    unicode_left = 4
                escape = False
                i += 1
                continue
            if ch == "\\":
                escape = True
                i += 1
                continue
            if ch == '"':
                in_string = False
                try:
                    decoded = json.loads('"' + token + '"')
                except json.JSONDecodeError as exc:
                    raise StreamingReplyAbort("ai_invalid_output") from exc
                if reading_reply:
                    return decoded
                if expecting_key:
                    current_key = decoded
                    expecting_key = False
                    expecting_colon = True
                else:
                    expecting_value_for = None
                i += 1
                continue
            token += ch
            if reading_reply:
                # Return decoded complete escape sequences available so far.
                try:
                    partial = json.loads('"' + token + '"')
                    reply_chars = [partial]
                except json.JSONDecodeError:
                    pass
            i += 1
        if reading_reply and reply_chars:
            return reply_chars[-1]
        return ""

    def feed_text(self, text: str) -> ReplyDelta | None:
        if self._aborted:
            raise StreamingReplyAbort("ai_safety_abort")
        if not text:
            return None
        self._buffer += text
        visible = self._decode_reply_prefix()
        if _has_forbidden(visible):
            self._aborted = True
            raise StreamingReplyAbort("ai_safety_abort")
        visible_prefix = self._emitted + self._holdback
        if not visible.startswith(visible_prefix):
            # Model rewrote earlier reply text; do not risk leaking stale partials.
            self._aborted = True
            raise StreamingReplyAbort("ai_invalid_output")
        pending = visible[len(visible_prefix):]
        if not pending:
            return None
        safe_joined = self._holdback + pending
        if _has_forbidden(safe_joined):
            self._aborted = True
            raise StreamingReplyAbort("ai_safety_abort")
        release_len = max(0, len(safe_joined) - HOLD_BACK_CHARS)
        release = safe_joined[:release_len]
        self._holdback = safe_joined[release_len:]
        if not release:
            return None
        self._emitted += release
        return ReplyDelta(release)

    def inspect_text(self, text: str) -> None:
        if self._aborted:
            raise StreamingReplyAbort("ai_safety_abort")
        if not text:
            return
        self._buffer += text
        visible = self._decode_reply_prefix()
        if _has_forbidden(visible):
            self._aborted = True
            raise StreamingReplyAbort("ai_safety_abort")
        visible_prefix = self._emitted + self._holdback
        if not visible.startswith(visible_prefix):
            self._aborted = True
            raise StreamingReplyAbort("ai_invalid_output")

    def flush(self) -> ReplyDelta | None:
        if self._holdback:
            if _has_forbidden(self._holdback):
                self._aborted = True
                raise StreamingReplyAbort("ai_safety_abort")
            release = self._holdback
            self._holdback = ""
            self._emitted += release
            return ReplyDelta(release)
        return None

    def final_parse(self, payload_or_text: dict[str, Any] | str | None) -> ParsedFastReply:
        candidate: dict[str, Any] | str
        if isinstance(payload_or_text, dict):
            candidate = payload_or_text
        else:
            candidate = payload_or_text if isinstance(payload_or_text, str) and payload_or_text.strip() else self._buffer
            if not isinstance(candidate, str) or not candidate.strip():
                raise FastReplyParseError("stream final payload is empty")
            if candidate.lstrip().startswith("```"):
                raise FastReplyParseError("markdown fenced JSON is not allowed")
        parsed = parse_openclaw_fast_reply(candidate)
        if not parsed.reply or not parsed.reply.strip():
            raise FastReplyParseError("reply is required")
        if _has_forbidden(parsed.reply):
            raise FastReplyParseError("reply contains forbidden internal term")
        return parsed

    def feed_event(self, event: object) -> ReplyDelta | None:
        if isinstance(event, ToolCallDetected):
            self._aborted = True
            raise StreamingReplyAbort("ai_unexpected_tool_call")
        if isinstance(event, StreamError):
            self._aborted = True
            raise StreamingReplyAbort(event.error_code)
        if isinstance(event, ContentDelta):
            self.inspect_text(event.text)
            return None
        if isinstance(event, Completed):
            return None
        return None
