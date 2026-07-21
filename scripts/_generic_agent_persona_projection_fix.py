from __future__ import annotations

import re
from pathlib import Path


path = Path("backend/app/services/ai_runtime_context.py")
text = path.read_text(encoding="utf-8")
start = re.search(r"^def _persona_context\(", text, flags=re.MULTILINE)
end = re.search(r"^def _row_text\(", text, flags=re.MULTILINE)
if start is None or end is None or end.start() <= start.start():
    raise SystemExit("persona context function boundary not found")

replacement = '''def _persona_context(profile: Any, match_rank: Any) -> dict[str, Any] | None:
    if (
        profile is None
        or not bool(getattr(profile, "is_active", False))
        or int(getattr(profile, "published_version", 0) or 0) <= 0
    ):
        return None
    raw_content = getattr(profile, "published_content_json", None)
    content = (
        sanitize_runtime_context(dict(raw_content))
        if isinstance(raw_content, dict)
        else {}
    )
    nested = content.get("identity_context")
    identity_source = dict(nested) if isinstance(nested, dict) else {}
    for field in (
        "brand_name",
        "assistant_name",
        "role_label",
        "identity_statement",
        "identity_answer_rule",
        "handoff_boundary",
        "tone",
        "capabilities",
        "guardrails",
        "disallowed_identity_claims",
    ):
        if field in content:
            identity_source[field] = content[field]
    identity = sanitize_runtime_context(identity_source)
    return {
        "profile_key": str(getattr(profile, "profile_key", "") or "")[:160],
        "name": str(getattr(profile, "name", "") or "")[:240],
        "summary": _sanitize_text(
            str(getattr(profile, "published_summary", "") or "")
        )[:1200],
        "content_json": content,
        "identity_context": identity if isinstance(identity, dict) else {},
        "published_version": int(
            getattr(profile, "published_version", 0) or 0
        ),
        "match_rank": match_rank,
    }


'''
text = text[: start.start()] + replacement + text[end.start() :]
path.write_text(text.rstrip() + "\n", encoding="utf-8")

updated = path.read_text(encoding="utf-8")
assert "published_content_json" in updated
assert '"identity_context": identity' in updated
