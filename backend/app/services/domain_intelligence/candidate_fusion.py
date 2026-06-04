from __future__ import annotations

from collections import OrderedDict
from typing import Iterable

from .schemas import RankedCandidate


def fuse_candidates(*groups: Iterable[RankedCandidate]) -> list[RankedCandidate]:
    merged: OrderedDict[str, RankedCandidate] = OrderedDict()
    for group in groups:
        for candidate in group:
            previous = merged.get(candidate.item_key)
            if previous is None or candidate.score > previous.score:
                merged[candidate.item_key] = candidate
    return sorted(merged.values(), key=lambda item: (-item.score, item.item_key))
