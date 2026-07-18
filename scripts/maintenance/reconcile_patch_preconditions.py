from __future__ import annotations

import textwrap
from pathlib import Path

path = Path(__file__).with_name("backend_convergence_patch.py")
source = path.read_text(encoding="utf-8")


def replace_section(start_marker: str, end_marker: str, replacement: str) -> None:
    global source
    start = source.index(start_marker)
    end = source.index(end_marker)
    source = source[:start] + textwrap.dedent(replacement) + source[end:]


replace_section(
    "# Worker business-health timestamps",
    "# Healthy single-writer local storage",
    r'''# Worker business-health timestamps are normalized at the authority boundary.
replace_once(
    "backend/app/services/queue_health.py",
    "from ..utils.time import utc_now\n",
    "from ..utils.time import ensure_utc, utc_now\n",
)
replace_once(
    "backend/app/services/queue_health.py",
    "def _age_ms(value, *, now) -> int | None:\n    if value is None:\n        return None\n    return max(0, int((now - value).total_seconds() * 1000))\n",
    "def _age_ms(value, *, now) -> int | None:\n    if value is None:\n        return None\n    return max(0, int((ensure_utc(now) - ensure_utc(value)).total_seconds() * 1000))\n",
)

''',
)

replace_section(
    "# Healthy single-writer local storage",
    "# Dedicated WebChat AI queue",
    r'''# Healthy single-writer local storage is a measured NO_CHANGE state.
replace_once(
    "scripts/qualification/infrastructure_decision.py",
    '''    if storage is None:
        object_reasons.append("storage_baseline_missing")
    if storage_backend == "s3" and storage_status == "ok":
        object_decision = "NO_CHANGE"
        object_reasons.append("object_storage_already_authoritative")
    elif storage is not None and (
        multi_writer_required is True
        or rpo_rto_met is False
        or capacity_breached is True
    ):
        object_decision = "CONSIDER_ADR"
        object_reasons.append("local_storage_boundary_confirmed")
    else:
        object_decision = "CONDITIONAL_HOLD"
        if not object_reasons:
            object_reasons.append("pilot_local_storage_boundary_not_exceeded")
''',
    '''    if storage is None:
        object_reasons.append("storage_baseline_missing")
    if storage_backend == "s3" and storage_status == "ok":
        object_decision = "NO_CHANGE"
        object_reasons.append("object_storage_already_authoritative")
    elif storage is not None and (
        multi_writer_required is True
        or rpo_rto_met is False
        or capacity_breached is True
    ):
        object_decision = "CONSIDER_ADR"
        object_reasons.append("local_storage_boundary_confirmed")
    elif storage is not None:
        object_decision = "NO_CHANGE"
        object_reasons.append("pilot_local_storage_boundary_not_exceeded")
    else:
        object_decision = "BLOCKED"
''',
)

path.write_text(source, encoding="utf-8")
