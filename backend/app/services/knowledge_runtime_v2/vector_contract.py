from __future__ import annotations

import math
from typing import Any

KNOWLEDGE_VECTOR_DIMENSION = 384


def postgres_vector_type() -> str:
    return f"vector({KNOWLEDGE_VECTOR_DIMENSION})"


def validate_embedding_dimension(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("knowledge_embedding_dimension_invalid")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("knowledge_embedding_dimension_invalid") from exc
    if parsed != KNOWLEDGE_VECTOR_DIMENSION:
        raise ValueError(
            f"knowledge_embedding_dimension_mismatch:{parsed}!={KNOWLEDGE_VECTOR_DIMENSION}"
        )
    return parsed


def validate_embedding_vector(value: Any, *, expected_dim: int = KNOWLEDGE_VECTOR_DIMENSION) -> list[float]:
    validate_embedding_dimension(expected_dim)
    if not isinstance(value, (list, tuple)):
        raise ValueError("knowledge_embedding_vector_not_sequence")
    if len(value) != expected_dim:
        raise ValueError(
            f"knowledge_embedding_vector_dimension_mismatch:{len(value)}!={expected_dim}"
        )
    normalized: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError("knowledge_embedding_vector_non_numeric")
        number = float(item)
        if not math.isfinite(number):
            raise ValueError("knowledge_embedding_vector_non_finite")
        normalized.append(number)
    return normalized


def embedding_is_current(
    row: Any,
    *,
    semantic_hash: str,
    model: str,
    expected_dim: int = KNOWLEDGE_VECTOR_DIMENSION,
) -> bool:
    try:
        validate_embedding_dimension(expected_dim)
        validate_embedding_vector(getattr(row, "embedding", None), expected_dim=expected_dim)
    except ValueError:
        return False
    return (
        getattr(row, "semantic_hash", None) == semantic_hash
        and getattr(row, "embedding_model", None) == model
        and int(getattr(row, "embedding_dim", 0) or 0) == expected_dim
        and getattr(row, "embedding_status", None) == "embedded"
    )
