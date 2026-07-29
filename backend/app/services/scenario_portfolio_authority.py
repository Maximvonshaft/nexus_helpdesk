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
    """Return selected definitions with a Catalog+Portfolio assignment identity."""

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
    """Converge automatic and explicit Assignment on the portfolio boundary.

    Full Catalog resolution remains authoritative for alias conflict detection.
    After resolution, an unselected scenario is rejected explicitly. Assignment
    snapshots are written with a digest/version that combines Catalog behavior
    and the portfolio selection that authorized production execution.
    """

    if getattr(case_scenario_service_module, "_portfolio_guard_installed", False):
        return

    original_candidate = case_scenario_service_module.resolve_candidate_scenario
    original_explicit = case_scenario_service_module.resolve_explicit_scenario
    original_assignment_values = case_scenario_service_module._assignment_values

    @wraps(original_candidate)
    def guarded_candidate(ticket, catalog, *, at=None):  # noqa: ANN001
        scenario = original_candidate(ticket, catalog, at=at)
        if scenario is None:
            return None
        try:
            require_selected_scenario(scenario.scenario_key)
        except ScenarioPortfolioError as exc:
            raise case_scenario_service_module._http_conflict(
                "case_scenario_outside_selected_portfolio",
                ticket_id=getattr(ticket, "id", None),
                scenario_key=scenario.scenario_key,
                portfolio_version=load_scenario_portfolio().version,
            ) from exc
        return scenario

    @wraps(original_explicit)
    def guarded_explicit(catalog, scenario_key, *, at=None):  # noqa: ANN001
        scenario = original_explicit(catalog, scenario_key, at=at)
        try:
            require_selected_scenario(scenario.scenario_key)
        except ScenarioPortfolioError as exc:
            raise case_scenario_service_module._http_conflict(
                "case_scenario_outside_selected_portfolio",
                scenario_key=scenario.scenario_key,
                portfolio_version=load_scenario_portfolio().version,
            ) from exc
        return scenario

    @wraps(original_assignment_values)
    def guarded_assignment_values(
        ticket,
        catalog,
        scenario,
        **kwargs,
    ):  # noqa: ANN001
        require_selected_scenario(scenario.scenario_key)
        return original_assignment_values(
            ticket,
            selected_runtime_catalog(catalog),
            scenario,
            **kwargs,
        )

    case_scenario_service_module.resolve_candidate_scenario = guarded_candidate
    case_scenario_service_module.resolve_explicit_scenario = guarded_explicit
    case_scenario_service_module._assignment_values = guarded_assignment_values
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
