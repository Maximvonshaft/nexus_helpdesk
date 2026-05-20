from typing import Dict, Type, Callable
from .schemas import ProviderCapabilities, ProviderRequest, ProviderResult
from sqlalchemy.orm import Session

class ProviderAdapter:
    name: str = "base"
    capabilities: ProviderCapabilities = ProviderCapabilities()

    async def generate(self, db: Session, request: ProviderRequest) -> ProviderResult:
        raise NotImplementedError

class ProviderRegistry:
    _factories: Dict[str, Callable[[Session], ProviderAdapter]] = {}

    @classmethod
    def register(cls, name: str, factory: Callable[[Session], ProviderAdapter]):
        cls._factories[name] = factory

    @classmethod
    def get(cls, name: str, db: Session) -> ProviderAdapter:
        factory = cls._factories.get(name)
        if factory:
            return factory(db)
        return None
