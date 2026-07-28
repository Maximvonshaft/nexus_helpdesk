from __future__ import annotations

from app.services.nexus_osr.case_context import (
    CaseContext,
    canonical_case_issue_type,
)


def test_tracking_shorthand_normalizes_to_published_scenario_alias():
    context = CaseContext(issue_type=" tracking ")

    assert canonical_case_issue_type("tracking") == "tracking_inquiry"
    assert context.issue_type == "tracking_inquiry"
    assert context.as_dict()["issue_type"] == "tracking_inquiry"


def test_existing_published_alias_remains_stable():
    context = CaseContext(issue_type="tracking_inquiry")

    assert context.issue_type == "tracking_inquiry"
