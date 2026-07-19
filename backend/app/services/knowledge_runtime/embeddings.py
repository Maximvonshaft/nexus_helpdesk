from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from ..runtime_endpoint_policy import require_http_endpoint


DIMENSION_REQUEST_CAPABILITY_ENV = "KNOWLEDGE_EMBEDDING_DIMENSION_REQUEST_SUPPORTED"
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class DeterministicHashEmbeddingProvider(EmbeddingProvider):
    def __init__(self, *, dim: int) -> None:
        self.dim = dim

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [_hash_embedding(text, dim=self.dim) for text in texts]


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dim: int,
        timeout_seconds: int,
        dimension_request_supported: bool = True,
    ) -> None:
        if not dimension_request_supported:
            raise ValueError("embedding_provider_dimension_request_unsupported")
        self.base_url = require_http_endpoint(base_url.rstrip("/"), label="Embedding provider endpoint")
        self.api_key = api_key
        self.model = model
        self.dim = dim
        self.timeout_seconds = timeout_seconds

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        body = json.dumps(
            {
                "model": self.model,
                "input": texts,
                "dimensions": self.dim,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            # Constructor restricts the endpoint to absolute HTTP(S) without embedded credentials.
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"embedding_provider_http_{exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("embedding_provider_unreachable") from exc
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != len(texts):
            raise RuntimeError("embedding_provider_invalid_response")
        vectors: list[list[float]] = []
        for row in sorted(data, key=lambda item: int(item.get("index", 0))):
            vector = row.get("embedding")
            if not isinstance(vector, list) or not vector:
                raise RuntimeError("embedding_provider_missing_vector")
            try:
                cleaned = [float(value) for value in vector]
            except (TypeError, ValueError) as exc:
                raise RuntimeError("embedding_provider_invalid_vector") from exc
            if any(not math.isfinite(value) for value in cleaned):
                raise RuntimeError("embedding_provider_invalid_vector")
            if self.dim and len(cleaned) != self.dim:
                raise RuntimeError("embedding_provider_dimension_mismatch")
            vectors.append(cleaned)
        return vectors


def get_embedding_provider(
    provider: str,
    *,
    dim: int,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    api_key_file: str | None = None,
    timeout_seconds: int = 20,
    dimension_request_supported: bool | None = None,
) -> EmbeddingProvider:
    if provider in {"deterministic_hash", "hash", "test"}:
        return DeterministicHashEmbeddingProvider(dim=dim)
    if provider == "openai_compatible":
        key = api_key or _read_secret_file(api_key_file)
        if not key:
            raise ValueError("missing_embedding_api_key")
        capability = (
            _dimension_request_supported_from_env()
            if dimension_request_supported is None
            else dimension_request_supported
        )
        return OpenAICompatibleEmbeddingProvider(
            base_url=base_url or "https://api.openai.com/v1",
            api_key=key,
            model=model or "text-embedding-3-small",
            dim=dim,
            timeout_seconds=timeout_seconds,
            dimension_request_supported=capability,
        )
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


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{float(value):.8f}" for value in vector) + "]"


def _dimension_request_supported_from_env() -> bool:
    value = os.getenv(DIMENSION_REQUEST_CAPABILITY_ENV, "true").strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ValueError("embedding_provider_dimension_request_capability_invalid")


def _read_secret_file(path: str | None) -> str | None:
    if not path:
        return None
    value = Path(path).read_text(encoding="utf-8").strip()
    return value or None


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
