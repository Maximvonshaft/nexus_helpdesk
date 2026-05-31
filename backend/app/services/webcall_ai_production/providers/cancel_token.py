from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CancelToken:
    reason: str | None = None

    @property
    def cancelled(self) -> bool:
        return self.reason is not None

    def cancel(self, reason: str = "cancelled") -> None:
        self.reason = reason
