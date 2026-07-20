from __future__ import annotations

import re
from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text.rstrip() + "\n", encoding="utf-8")


def function_bounds(text: str, name: str) -> tuple[int, int]:
    match = re.search(rf"^def {re.escape(name)}\(", text, flags=re.MULTILINE)
    if match is None:
        raise SystemExit(f"function not found: {name}")
    next_match = re.search(
        r"^def [A-Za-z0-9_]+\(",
        text[match.end():],
        flags=re.MULTILINE,
    )
    end = len(text) if next_match is None else match.end() + next_match.start()
    while end > match.start() and text[end - 1] == "\n":
        end -= 1
    return match.start(), end


def remove_function(text: str, name: str) -> str:
    start, end = function_bounds(text, name)
    return text[:start].rstrip() + "\n\n\n" + text[end:].lstrip("\n")


# The dispatcher was physically retired. Its authority guarantees are covered
# by Agent Runtime architecture, Provider Router and bounded-audit tests.
for path in (
    "backend/tests/test_provider_runtime_dispatcher_authority.py",
    "backend/tests/test_webchat_tracking_reply_polish.py",
):
    target = Path(path)
    if target.exists():
        target.unlink()

# Preserve card, idempotency, safety and outbound semantics tests. Remove only
# tests of the deleted business-specific runtime parser.
structured_path = "backend/tests/test_webchat_structured_runtime.py"
structured = read(structured_path)
structured = structured.replace(
    "from app.services.webchat_runtime_output_parser import "
    "RuntimeReplyParseError, parse_runtime_reply_provider_output\n",
    "",
    1,
)
for obsolete in (
    "test_runtime_parser_cleans_mixed_waybill_label",
    "test_runtime_parser_allows_shipment_outcome_only_with_tracking_evidence",
):
    structured = remove_function(structured, obsolete)
write(structured_path, structured)

# TrackingFact remains a Tool backend. Remove only the retired history/policy
# heuristic that selected server-side prefetch before the model turn.
tracking_path = "backend/tests/test_webchat_tracking_fact_mvp.py"
tracking = read(tracking_path)
tracking = tracking.replace(
    "from app.services.webchat_ai_service import "
    "_allows_history_tracking_lookup, _looks_like_service_policy_question\n",
    "",
    1,
)
tracking = remove_function(
    tracking,
    "test_service_policy_question_does_not_inherit_history_tracking_lookup",
)
write(tracking_path, tracking)

assert not Path(
    "backend/tests/test_provider_runtime_dispatcher_authority.py"
).exists()
assert not Path("backend/tests/test_webchat_tracking_reply_polish.py").exists()
assert "webchat_runtime_output_parser" not in read(structured_path)
assert "_allows_history_tracking_lookup" not in read(tracking_path)
