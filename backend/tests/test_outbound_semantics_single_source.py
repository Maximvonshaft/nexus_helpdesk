from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_compatibility_module_is_retired() -> None:
    compatibility = ROOT / "app/services/outbound_message_semantics.py"
    canonical = ROOT / "app/services/outbound_semantics.py"

    assert not compatibility.exists()
    assert canonical.is_file()
    content = canonical.read_text(encoding="utf-8")
    assert "def count_outbound_semantics" in content
    assert "def outbound_ui_label" in content


def test_business_code_uses_canonical_outbound_semantics() -> None:
    offenders: list[str] = []
    for path in (ROOT / "app").rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        if "outbound_message_semantics" in content:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
