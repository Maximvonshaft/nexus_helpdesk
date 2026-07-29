from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import event

from ..models_case_scenario import CaseScenarioAssignment

PORTFOLIO_SCHEMA = "nexus.golden-journeys.v2"
PORTFOLIO_PATH = (
    Path(__file__).resolve().parents[3]
    / "config"
    / "product"
    / "golden-journeys.v1.json"
)


@dataclass(frozen=True)
class ScenarioPortfolio:
    schema: str
    version: str
    selected_scenario_keys: frozenset[str]
    source_sha256: str

    def contains(self, scenario_key: str) -> bool:
        return str(scenario_key or "").strip().lower() in self.selected_scenario_keys


class ScenarioPortfolioError(ValueError):
    pass


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@lru_cache(maxsize=1)
def load_scenario_portfolio() -> ScenarioPortfolio:
    try:
        payload = json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScenarioPortfolioError("scenario_portfolio_unavailable") from exc
    if not isinstance(payload, dict) or payload.get("schema") != PORTFOLIO_SCHEMA:
        raise ScenarioPortfolioError("scenario_portfolio_schema_invalid")
    version = str(payload.get("version") or "").strip()
    rows = payload.get("selected_scenarios")
    if not version or not isinstance(rows, list) or not rows:
        raise ScenarioPortfolioError("scenario_portfolio_selection_invalid")

    keys: list[str] = []
    launch_orders: list[int] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ScenarioPortfolioError("scenario_portfolio_selection_invalid")
        key = str(row.get("scenario_key") or "").strip().lower()
        try:
            launch_order = int(row.get("launch_order"))
        except (TypeError, ValueError) as exc:
            raise ScenarioPortfolioError(
                "scenario_portfolio_launch_order_invalid"
            ) from exc
        if not key or launch_order <= 0:
            raise ScenarioPortfolioError("scenario_portfolio_selection_invalid")
        keys.append(key)
        launch_orders.append(launch_order)
    if len(keys) != len(set(keys)):
        raise ScenarioPortfolioError("scenario_portfolio_duplicate_scenario")
    if sorted(launch_orders) != list(range(1, len(launch_orders) + 1)):
        raise ScenarioPortfolioError("scenario_portfolio_launch_order_invalid")

    return ScenarioPortfolio(
        schema=PORTFOLIO_SCHEMA,
        version=version,
        selected_scenario_keys=frozenset(keys),
        source_sha256=hashlib.sha256(_canonical_json(payload)).hexdigest(),
    )


def require_selected_scenario(scenario_key: str) -> None:
    key = str(scenario_key or "").strip().lower()
    portfolio = load_scenario_portfolio()
    if key not in portfolio.selected_scenario_keys:
        raise ScenarioPortfolioError(
            f"case_scenario_outside_selected_portfolio:{key or 'missing'}"
        )


def portfolio_projection() -> dict[str, Any]:
    portfolio = load_scenario_portfolio()
    return {
        "schema": portfolio.schema,
        "version": portfolio.version,
        "source_sha256": portfolio.source_sha256,
        "selected_scenario_keys": sorted(portfolio.selected_scenario_keys),
    }


@event.listens_for(CaseScenarioAssignment, "before_insert")
def _enforce_assignment_portfolio(
    _mapper,
    _connection,
    target: CaseScenarioAssignment,
) -> None:  # noqa: ANN001
    """Guard every new production Scenario Assignment at the sole writer edge.

    Existing rows and their immutable snapshots remain valid. Automatic
    classification, explicit reclassification and any future routing writer all
    converge here; an unselected Catalog capability cannot enter the production
    state machine merely because its lifecycle is approved.
    """

    require_selected_scenario(target.scenario_key)


__all__ = [
    "PORTFOLIO_PATH",
    "ScenarioPortfolio",
    "ScenarioPortfolioError",
    "load_scenario_portfolio",
    "portfolio_projection",
    "require_selected_scenario",
]
