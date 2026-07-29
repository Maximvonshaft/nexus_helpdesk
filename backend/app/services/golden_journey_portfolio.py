from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.orm import Session

from ..models_case_scenario import CaseScenarioAssignment

_PORTFOLIO_SCHEMA = "nexus.golden-journeys.v2"
_PORTFOLIO_PATH = (
    Path(__file__).resolve().parents[3]
    / "config"
    / "product"
    / "golden-journeys.v1.json"
)
_INSTALLED = False


class GoldenJourneyPortfolioError(ValueError):
    pass


@lru_cache(maxsize=1)
def selected_scenario_keys() -> frozenset[str]:
    try:
        payload = json.loads(_PORTFOLIO_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoldenJourneyPortfolioError(
            "golden_journey_portfolio_unavailable"
        ) from exc
    if payload.get("schema") != _PORTFOLIO_SCHEMA:
        raise GoldenJourneyPortfolioError("golden_journey_portfolio_schema_invalid")
    rows = payload.get("selected_scenarios")
    if not isinstance(rows, list) or not rows:
        raise GoldenJourneyPortfolioError("golden_journey_portfolio_empty")
    keys = [
        str(row.get("scenario_key") or "").strip()
        for row in rows
        if isinstance(row, dict)
    ]
    if any(not key for key in keys) or len(keys) != len(set(keys)):
        raise GoldenJourneyPortfolioError("golden_journey_portfolio_identity_invalid")
    return frozenset(keys)


def require_selected_scenario(scenario_key: str) -> None:
    normalized = str(scenario_key or "").strip()
    if normalized not in selected_scenario_keys():
        raise GoldenJourneyPortfolioError(
            f"scenario_outside_selected_portfolio:{normalized or 'missing'}"
        )


def _enforce_portfolio_before_flush(
    session: Session,
    _flush_context,
    _instances,
) -> None:
    for row in session.new:
        if isinstance(row, CaseScenarioAssignment):
            require_selected_scenario(row.scenario_key)


def install_golden_journey_portfolio_guard() -> None:
    """Reject every new Scenario assignment outside the approved portfolio."""

    global _INSTALLED
    if _INSTALLED:
        return
    # Fail startup before serving requests when the portfolio is malformed.
    selected_scenario_keys()
    event.listen(Session, "before_flush", _enforce_portfolio_before_flush)
    _INSTALLED = True


__all__ = [
    "GoldenJourneyPortfolioError",
    "install_golden_journey_portfolio_guard",
    "require_selected_scenario",
    "selected_scenario_keys",
]
