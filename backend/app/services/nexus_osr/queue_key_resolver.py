from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ...models_osr import HumanHoursPolicyRecord

DEFAULT_QUEUE_KEY = "default"
GLOBAL_COUNTRY = "GLOBAL"
ALL_CHANNEL = "all"


@dataclass(frozen=True)
class QueueKeyResolution:
    queue_key: str
    source: str
    country_code: str
    channel: str
    tenant_id: str | None = None
    language: str | None = None
    issue_type: str | None = None
    matched_policy_id: int | None = None
    match_score: int = 0
    fallback: bool = False

    def as_safe_dict(self) -> dict[str, Any]:
        return {
            "queue_key": self.queue_key,
            "source": self.source,
            "country_code": self.country_code,
            "channel": self.channel,
            "tenant_id": self.tenant_id,
            "language": self.language,
            "issue_type": self.issue_type,
            "matched_policy_id": self.matched_policy_id,
            "match_score": self.match_score,
            "fallback": self.fallback,
        }


def resolve_queue_key(
    db: Session,
    *,
    country_code: str | None = None,
    channel: str | None = None,
    language: str | None = None,
    issue_type: str | None = None,
    tenant_id: str | None = None,
    default_queue_key: str = DEFAULT_QUEUE_KEY,
) -> QueueKeyResolution:
    """Resolve the human-support queue key from policy rows.

    The current OSR persistence schema scopes human-hours policies by country and
    channel. Language, issue type, and tenant are accepted and carried in the
    resolution trace so later policy versions can add those dimensions without
    changing the orchestration entrypoint. This resolver does not hard-code any
    country, language, queue, or issue behavior.
    """

    requested_country = _country(country_code)
    requested_channel = _channel(channel)
    rows = (
        db.query(HumanHoursPolicyRecord)
        .filter(HumanHoursPolicyRecord.enabled.is_(True))
        .filter(HumanHoursPolicyRecord.country_code.in_([requested_country, GLOBAL_COUNTRY]))
        .filter(HumanHoursPolicyRecord.channel.in_([requested_channel, ALL_CHANNEL]))
        .all()
    )
    if not rows:
        return QueueKeyResolution(
            queue_key=default_queue_key,
            source="default_fallback",
            country_code=requested_country,
            channel=requested_channel,
            tenant_id=tenant_id,
            language=language,
            issue_type=issue_type,
            fallback=True,
        )

    selected = max(rows, key=lambda row: (_score(row, requested_country, requested_channel), -int(row.id or 0)))
    score = _score(selected, requested_country, requested_channel)
    return QueueKeyResolution(
        queue_key=selected.queue_key or default_queue_key,
        source="human_hours_policy",
        country_code=requested_country,
        channel=requested_channel,
        tenant_id=tenant_id,
        language=language,
        issue_type=issue_type,
        matched_policy_id=selected.id,
        match_score=score,
        fallback=False,
    )


def _country(value: str | None) -> str:
    cleaned = str(value or "").strip().upper()
    return cleaned or GLOBAL_COUNTRY


def _channel(value: str | None) -> str:
    cleaned = str(value or "").strip().lower()
    return cleaned or ALL_CHANNEL


def _score(row: HumanHoursPolicyRecord, requested_country: str, requested_channel: str) -> int:
    score = 0
    if str(row.country_code or "").upper() == requested_country:
        score += 20
    if str(row.channel or "").lower() == requested_channel:
        score += 10
    return score
