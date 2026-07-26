from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUTHORITY = ROOT / "config/architecture/business-aggregate-authority.v1.json"


def _python_models() -> list[Path]:
    return sorted((ROOT / "backend/app").glob("*models*.py")) + [
        ROOT / "backend/app/models.py",
        ROOT / "backend/app/operator_models.py",
    ]


def test_machine_readable_business_authority_is_unambiguous():
    payload = json.loads(AUTHORITY.read_text(encoding="utf-8"))
    assert payload["schema"] == "nexus.business-aggregate-authority.v1"
    assert payload["durable_case"]["model"] == "app.models.Ticket"
    assert payload["durable_case"]["table"] == "tickets"
    assert payload["live_conversation"]["ticket_optional"] is True
    assert payload["live_handoff"]["model"].endswith("WebchatHandoffRequest")
    assert payload["operator_queue_projection"]["source_of_truth"] is False
    assert payload["operator_queue_projection"]["rebuildable"] is True
    assert payload["operator_queue_projection"]["may_mutate_source"] is False


def test_no_second_case_model_or_table_exists():
    offenders: list[str] = []
    seen: set[Path] = set()
    for path in _python_models():
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "Case":
                offenders.append(f"{path.relative_to(ROOT)}:class Case")
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__tablename__":
                        if isinstance(node.value, ast.Constant) and node.value.value == "cases":
                            offenders.append(f"{path.relative_to(ROOT)}:cases")
    assert offenders == []


def test_product_documents_use_ticket_as_case_semantics():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    product = (ROOT / "webapp/PRODUCT.md").read_text(encoding="utf-8")
    adr = (ROOT / "docs/architecture/adr-001-ticket-as-case-authority.md").read_text(
        encoding="utf-8"
    )

    for source in (readme, product, adr):
        assert "Ticket-as-Case" in source
        assert "OperatorTask" in source
        assert "WebchatHandoffRequest" in source

    assert "every customer contact is a case" not in product.lower()
    assert "A live contact without a Ticket is a **conversation**" in adr
