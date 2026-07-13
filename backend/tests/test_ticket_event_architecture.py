from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
_ALLOWED_CONSTRUCTORS = {
    (APP_ROOT / "models.py").resolve(),
    (APP_ROOT / "services" / "ticket_event_writer.py").resolve(),
}


@dataclass(frozen=True)
class DirectConstruction:
    path: str
    line: int
    symbol: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: direct {self.symbol}(...) bypasses TicketEventWriter"


def _module_is_models(node: ast.ImportFrom) -> bool:
    module = node.module or ""
    return module == "models" or module.endswith(".models")


def _module_can_export_models(node: ast.ImportFrom) -> bool:
    module = node.module or ""
    return not module or module == "app" or module.endswith(".app")


def _attribute_path(node: ast.AST) -> tuple[str, ...] | None:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        parent = _attribute_path(node.value)
        if parent is None:
            return None
        return (*parent, node.attr)
    return None


def find_direct_ticket_event_construction(root: Path = APP_ROOT) -> list[str]:
    findings: list[DirectConstruction] = []
    for path in sorted(root.rglob("*.py")):
        resolved = path.resolve()
        if resolved in _ALLOWED_CONSTRUCTORS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        direct_names: set[str] = set()
        model_module_paths: set[tuple[str, ...]] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and _module_is_models(node):
                for alias in node.names:
                    if alias.name == "TicketEvent":
                        direct_names.add(alias.asname or alias.name)
                    elif alias.name == "*":
                        direct_names.add("TicketEvent")
            elif isinstance(node, ast.ImportFrom) and _module_can_export_models(node):
                for alias in node.names:
                    if alias.name == "models":
                        model_module_paths.add((alias.asname or alias.name,))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "app.models" or alias.name.endswith(".models"):
                        if alias.asname:
                            model_module_paths.add((alias.asname,))
                        else:
                            model_module_paths.add(tuple(alias.name.split(".")))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            symbol = None
            if isinstance(node.func, ast.Name) and node.func.id in direct_names:
                symbol = node.func.id
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "TicketEvent":
                receiver_path = _attribute_path(node.func.value)
                if receiver_path in model_module_paths:
                    symbol = ".".join((*receiver_path, "TicketEvent"))
            if symbol is None:
                continue
            findings.append(
                DirectConstruction(
                    path=path.relative_to(root.parent).as_posix(),
                    line=node.lineno,
                    symbol=symbol,
                )
            )
    return [
        finding.render()
        for finding in sorted(
            findings, key=lambda item: (item.path, item.line, item.symbol)
        )
    ]


def test_production_code_cannot_construct_ticket_event_outside_writer() -> None:
    violations = find_direct_ticket_event_construction()
    assert violations == [], "Forbidden durable audit writes:\n" + "\n".join(violations)


def test_architecture_scanner_detects_direct_and_aliased_imports(
    tmp_path: Path,
) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    (app_root / "direct.py").write_text(
        "from app.models import TicketEvent\nrow = TicketEvent(ticket_id=1)\n",
        encoding="utf-8",
    )
    (app_root / "aliased.py").write_text(
        "from app.models import TicketEvent as AuditRow\nrow = AuditRow(ticket_id=1)\n",
        encoding="utf-8",
    )
    (app_root / "module_alias.py").write_text(
        "import app.models as models\nrow = models.TicketEvent(ticket_id=1)\n",
        encoding="utf-8",
    )
    (app_root / "module_unaliased.py").write_text(
        "import app.models\nrow = app.models.TicketEvent(ticket_id=1)\n",
        encoding="utf-8",
    )
    (app_root / "from_app_models.py").write_text(
        "from app import models\nrow = models.TicketEvent(ticket_id=1)\n",
        encoding="utf-8",
    )
    (app_root / "star_import.py").write_text(
        "from app.models import *\nrow = TicketEvent(ticket_id=1)\n",
        encoding="utf-8",
    )
    (app_root / "query_only.py").write_text(
        "from app.models import TicketEvent\ndef query(db):\n    return db.query(TicketEvent).first()\n",
        encoding="utf-8",
    )

    violations = find_direct_ticket_event_construction(app_root)

    assert len(violations) == 6
    assert any("app/direct.py:2" in item for item in violations)
    assert any("app/aliased.py:2" in item for item in violations)
    assert any("app/module_alias.py:2" in item for item in violations)
    assert any("app/module_unaliased.py:2" in item for item in violations)
    assert any("app/from_app_models.py:2" in item for item in violations)
    assert any("app/star_import.py:2" in item for item in violations)
    assert not any("query_only.py" in item for item in violations)
