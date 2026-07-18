from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from sqlalchemy.orm import Session

from .schemas import ProviderCapabilities, ProviderRequest, ProviderResult


class ProviderAdapter:
    name: str = "base"
    capabilities: ProviderCapabilities = ProviderCapabilities()

    async def generate(self, db: Session, request: ProviderRequest) -> ProviderResult:
        raise NotImplementedError


class ProviderRegistry:
    _factories: ClassVar[dict[str, Callable[[Session], ProviderAdapter]]] = {}

    @classmethod
    def register(cls, name: str, factory: Callable[[Session], ProviderAdapter]) -> None:
        cls._factories[name] = factory

    @classmethod
    def get(cls, name: str, db: Session) -> ProviderAdapter | None:
        factory = cls._factories.get(name)
        return factory(db) if factory is not None else None
