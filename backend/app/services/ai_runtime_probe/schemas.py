from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CodexTokenProbeResult:
    ok: bool
    provider: str
    transport: str
    elapsed_ms: int
    parse_ok: bool
    error_code: str | None = None
    safe_error: str | None = None
    mock: bool = False
    raw_payload_safe_summary: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
