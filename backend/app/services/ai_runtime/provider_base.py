from __future__ import annotations

from abc import ABC, abstractmethod

from ..webchat_fast_config import WebchatFastSettings
from .schemas import FastAIProviderRequest, FastAIProviderResult


class BaseFastAIProvider(ABC):
    name: str

    def __init__(self, settings: WebchatFastSettings) -> None:
        self.settings = settings

    @abstractmethod
    def is_configured(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def generate(self, request: FastAIProviderRequest) -> FastAIProviderResult:
        raise NotImplementedError
