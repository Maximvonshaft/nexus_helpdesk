from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache, wraps
from pathlib import Path
from typing import Any

from sqlalchemy import event

from ..models_case_scenario import CaseScenarioAssignment
from .nexus_osr.business_scenarios import BusinessScenarioCatalog

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


def selected_runtime_catalog(
    catalog: BusinessScenarioCatalog,
) -> BusinessScenarioCatalog:
    """Project the Catalog through the executable product portfolio.

    Scenario behavior remains owned by the Business Scenario Catalog. The
    portfolio contributes only selection and a digest-bound catalog identity so
    every immutable Assignment proves both the behavior revision and the product
    scope that authorized it.
    """

    portfolio = load_scenario_portfolio()
    by_key = catalog.by_key()
    missing = sorted(portfolio.selected_scenario_keys - set(by_key))
    if missing:
        raise ScenarioPortfolioError(
            "scenario_portfolio_catalog_reference_missing:" + ",".join(missing)
        )
    selected = tuple(
        item
        for item in catalog.scenarios
        if item.scenario_key in portfolio.selected_scenario_keys
    )
    if len(selected) != len(portfolio.selected_scenario_keys):
        raise ScenarioPortfolioError("scenario_portfolio_projection_incomplete")
    combined_digest = hashlib.sha256(
        f"{catalog.source_sha256}:{portfolio.source_sha256}".encode("utf-8")
    ).hexdigest()
    return BusinessScenarioCatalog(
        schema=catalog.schema,
        catalog_version=(
            f"{catalog.catalog_version}+portfolio.{portfolio.version}"
        )[:120],
        owner=catalog.owner,
        approved_at=catalog.approved_at,
        scope_mode=catalog.scope_mode,
        scenarios=selected,
        source_sha256=combined_digest,
    )


def install_runtime_portfolio_guard(case_scenario_service_module) -> None:  # noqa: ANN001
    """Install selection into the sole Assignment service.

    The model family imports this after ``case_scenario_service`` has registered
    lifecycle listeners. Replacing its runtime catalog loader affects automatic
    Core inserts and explicit ORM reclassification without creating another
    Assignment writer or modifying historical snapshot reads.
    """

    if getattr(case_scenario_service_module, "_portfolio_guard_installed", False):
        return
    original_loader = case_scenario_service_module.load_runtime_scenario_catalog

    @wraps(original_loader)
    def guarded_loader(*, at=None):  # noqa: ANN001
        return selected_runtime_catalog(original_loader(at=at))

    case_scenario_service_module.load_runtime_scenario_catalog = guarded_loader
    case_scenario_service_module._portfolio_guard_installed = True


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
    """Guard direct ORM writes in addition to the runtime catalog projection."""

    require_selected_scenario(target.scenario_key)


__all__ = [
    "PORTFOLIO_PATH",
    "ScenarioPortfolio",
    "ScenarioPortfolioError",
    "install_runtime_portfolio_guard",
    "load_scenario_portfolio",
    "portfolio_projection",
    "require_selected_scenario",
    "selected_runtime_catalog",
]
