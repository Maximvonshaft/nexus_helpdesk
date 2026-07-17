from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from sqlalchemy.orm import Session

from .output_contracts import OutputContracts
from .schemas import ProviderCapabilities, ProviderRequest, ProviderResult


class ProviderAdapter:
    name: str = "base"
    capabilities: ProviderCapabilities = ProviderCapabilities()

    async def generate(self, db: Session, request: ProviderRequest) -> ProviderResult:
        raise NotImplementedError


class _ValidatedProviderAdapter(ProviderAdapter):
    """Single fail-closed boundary before any registered adapter can execute."""

    def __init__(self, delegate: ProviderAdapter):
        self._delegate = delegate
        self.name = delegate.name
        self.capabilities = delegate.capabilities

    async def generate(self, db: Session, request: ProviderRequest) -> ProviderResult:
        if not OutputContracts.get_schema(request.output_contract):
            return ProviderResult.unavailable(
                self.name,
                "provider_runtime_output_contract_invalid",
                0,
                fallback_allowed=False,
            )
        return await self._delegate.generate(db, request)


class ProviderRegistry:
    _factories: ClassVar[dict[str, Callable[[Session], ProviderAdapter]]] = {}

    @classmethod
    def register(cls, name: str, factory: Callable[[Session], ProviderAdapter]) -> None:
        cls._factories[name] = factory

    @classmethod
    def get(cls, name: str, db: Session) -> ProviderAdapter | None:
        factory = cls._factories.get(name)
        if factory is None:
            return None
        return _ValidatedProviderAdapter(factory(db))
