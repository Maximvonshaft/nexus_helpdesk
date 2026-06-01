from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class DeterministicHashEmbeddingProvider(EmbeddingProvider):
    def __init__(self, *, dim: int) -> None:
        self.dim = dim

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [_hash_embedding(text, dim=self.dim) for text in texts]


def get_embedding_provider(provider: str, *, dim: int) -> EmbeddingProvider:
    if provider in {"deterministic_hash", "hash", "test"}:
        return DeterministicHashEmbeddingProvider(dim=dim)
    raise ValueError(f"unsupported_embedding_provider:{provider}")


def cosine_similarity(left: list[float] | None, right: list[float] | None) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return dot / (left_norm * right_norm)


def semantic_hash(text: str) -> str:
    normalized = " ".join(str(text or "").lower().split())
    return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()


def _hash_embedding(text: str, *, dim: int) -> list[float]:
    vector = [0.0] * dim
    tokens = re.findall(r"[\w\u4e00-\u9fff]{2,}", str(text or "").lower())
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8", errors="ignore")).digest()
        index = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [round(value / norm, 6) for value in vector]
