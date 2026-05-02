from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_compatibility_module_is_reexport_only():
    path = ROOT / "app/services/outbound_message_semantics.py"
    content = path.read_text(encoding="utf-8")
    assert "Canonical implementation lives" in content
    assert "from .outbound_semantics import *" in content
    forbidden_fragments = [
        "EXTERNAL_OUTBOUND_CHANNELS =",
        "WEBCHAT_LOCAL_ACK_PROVIDER_STATUSES =",
        "def count_outbound_semantics",
        "def outbound_ui_label",
    ]
    for fragment in forbidden_fragments:
        assert fragment not in content


def test_business_code_uses_canonical_outbound_semantics():
    offenders: list[str] = []
    for path in (ROOT / "app").rglob("*.py"):
        if path.name == "outbound_message_semantics.py":
            continue
        content = path.read_text(encoding="utf-8")
        if "outbound_message_semantics" in content:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
