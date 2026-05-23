from __future__ import annotations

from collections import defaultdict
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.main import app


def test_no_duplicate_canonical_api_routes():
    seen: dict[tuple[str, str], list[str]] = defaultdict(list)
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        endpoint = getattr(route, "endpoint", None)
        if not path or not methods or not path.startswith("/api/"):
            continue
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            seen[(method, path)].append(getattr(endpoint, "__name__", "<unknown>"))

    duplicates = {
        f"{method} {path}": names
        for (method, path), names in sorted(seen.items())
        if len(names) > 1
    }
    assert duplicates == {}
