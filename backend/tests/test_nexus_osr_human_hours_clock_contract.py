from __future__ import annotations

from datetime import datetime, timezone

from app.services.nexus_osr.policies import HumanHoursPolicy


def _all_day_policy() -> HumanHoursPolicy:
    return HumanHoursPolicy(
        queue_key="zz-webchat",
        timezone_name="UTC",
        weekly_hours={
            day: [("00:00", "23:59")]
            for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
        },
    )


def test_human_hours_daytime_is_online_with_explicit_now() -> None:
    result = _all_day_policy().evaluate(
        datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)
    )
    assert result.is_online is True
    assert result.reason == "within_working_hours"


def test_human_hours_235930_is_offline_under_minute_precision_contract() -> None:
    result = _all_day_policy().evaluate(
        datetime(2026, 7, 6, 23, 59, 30, tzinfo=timezone.utc)
    )
    assert result.is_online is False
    assert result.reason == "outside_working_hours"
