from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .schemas import DomainIntent


@dataclass(frozen=True)
class DomainPack:
    key: str
    description: str
    intents: tuple[DomainIntent, ...]

    def intent_by_key(self, key: str) -> DomainIntent | None:
        normalized = key.strip().lower()
        for intent in self.intents:
            if intent.key == normalized or intent.full_key == normalized:
                return intent
        return None


class DomainRegistry:
    def __init__(self, packs: Iterable[DomainPack] = ()) -> None:
        self._packs: dict[str, DomainPack] = {}
        for pack in packs:
            self.register(pack)

    def register(self, pack: DomainPack) -> None:
        key = pack.key.strip().lower()
        if not key:
            raise ValueError("domain pack key is required")
        self._packs[key] = pack

    def get(self, key: str | None) -> DomainPack | None:
        if not key:
            return None
        return self._packs.get(key.strip().lower())

    def all_packs(self) -> tuple[DomainPack, ...]:
        return tuple(self._packs.values())

    def all_intents(self) -> tuple[DomainIntent, ...]:
        return tuple(intent for pack in self._packs.values() for intent in pack.intents)

    def find_intent(self, full_or_local_key: str) -> DomainIntent | None:
        normalized = full_or_local_key.strip().lower()
        if not normalized:
            return None
        if "." in normalized:
            domain, local = normalized.split(".", 1)
            pack = self.get(domain)
            return pack.intent_by_key(local) if pack else None
        for pack in self._packs.values():
            intent = pack.intent_by_key(normalized)
            if intent:
                return intent
        return None
