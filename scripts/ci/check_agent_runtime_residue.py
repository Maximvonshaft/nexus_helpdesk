#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGETS = (
    ROOT / "backend/app/services/provider_runtime",
    ROOT / "backend/app/services/webchat_runtime_ai_service.py",
    ROOT / "backend/app/services/webchat_ai_service.py",
    ROOT / "backend/app/services/ai_runtime_context.py",
    ROOT / "backend/app/services/webchat_runtime_output_parser.py",
    ROOT / "backend/app/services/webchat_ai_decision_runtime/policy_gate.py",
)
FORBIDDEN = (
    "shipment_status_without_evidence",
    "_contains_live_shipment_conclusion",
    "_maybe_lookup_tracking_fact",
    "_HISTORY_TRACKING_CONTEXT_MARKERS",
    "_UNVERIFIED_SHIPMENT_OUTCOME_PATTERNS",
    "tracking_fact_summary",
    "tracking_fact_evidence_present",
    "locked_fact_grounding_conflict",
    "tracking_status_without_trusted_fact",
)


def iter_python_files(path: Path):
    if path.is_file():
        yield path
    elif path.exists():
        yield from path.rglob("*.py")


def main() -> int:
    failures: list[str] = []
    for target in TARGETS:
        for path in iter_python_files(target):
            text = path.read_text(encoding="utf-8")
            for marker in FORBIDDEN:
                if marker in text:
                    failures.append(f"{path.relative_to(ROOT)}: contains retired marker {marker}")
    if failures:
        print("\n".join(failures))
        return 1
    print("Agent Runtime residue check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
