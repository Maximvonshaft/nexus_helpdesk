from __future__ import annotations

import re
import unicodedata
from collections import OrderedDict
from typing import Iterable

from .schemas import DomainIntent, QueryRewriteResult


_LOW_SIGNAL = {"", "hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "32"}


def normalize_query(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def is_low_signal_query(value: str | None) -> bool:
    normalized = normalize_query(value)
    if normalized in _LOW_SIGNAL:
        return True
    return len(normalized) <= 2 and not any("\u4e00" <= ch <= "\u9fff" for ch in normalized)


def rewrite_query(query: str | None, intents: Iterable[DomainIntent] = ()) -> QueryRewriteResult:
    normalized = normalize_query(query)
    terms: OrderedDict[str, None] = OrderedDict()
    for intent in intents:
        for term in (*intent.rewrite_terms, *intent.aliases):
            cleaned = normalize_query(term)
            if cleaned and cleaned not in normalized:
                terms[cleaned] = None
    rewrite_terms = tuple(terms.keys())
    expanded = " ".join(part for part in (normalized, " ".join(rewrite_terms)) if part).strip()
    return QueryRewriteResult(normalized_query=normalized, rewrite_terms=rewrite_terms, expanded_query=expanded)
