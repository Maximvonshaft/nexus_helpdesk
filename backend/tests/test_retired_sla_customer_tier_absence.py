from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOTS = (
    ROOT / "backend" / "app",
    ROOT / "config",
    ROOT / "webapp" / "src",
)
TEXT_SUFFIXES = {".py", ".json", ".yaml", ".yml", ".ts", ".tsx"}


def test_retired_sla_customer_tier_is_absent_from_runtime_surfaces():
    findings: list[str] = []
    for root in RUNTIME_ROOTS:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            if "customer_tier" in text:
                findings.append(path.relative_to(ROOT).as_posix())
    assert findings == []
