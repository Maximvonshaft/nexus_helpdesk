from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: str, old: str, new: str, label: str) -> None:
    target = ROOT / path
    content = target.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    target.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "backend/app/services/operator_queue_scope.py",
    """    policy_fingerprint = capability_fingerprint(current_user, session)\n    if grant is None:\n        raw = f\"user:{int(current_user.id)}:capabilities:{policy_fingerprint}:grant:none\"\n    else:\n        updated = grant.updated_at.isoformat() if isinstance(grant.updated_at, datetime) else str(grant.updated_at)\n        raw = (\n            f\"user:{int(current_user.id)}:capabilities:{policy_fingerprint}:\"\n            f\"grant:{grant.id}:{updated}:{int(bool(grant.enabled))}\"\n        )\n""",
    """    policy_fingerprint = capability_fingerprint(current_user, session)\n    team_identity = getattr(current_user, \"team_id\", None) or \"none\"\n    if grant is None:\n        raw = (\n            f\"user:{int(current_user.id)}:team:{team_identity}:\"\n            f\"capabilities:{policy_fingerprint}:grant:none\"\n        )\n    else:\n        updated = grant.updated_at.isoformat() if isinstance(grant.updated_at, datetime) else str(grant.updated_at)\n        raw = (\n            f\"user:{int(current_user.id)}:team:{team_identity}:\"\n            f\"capabilities:{policy_fingerprint}:\"\n            f\"grant:{grant.id}:{updated}:{int(bool(grant.enabled))}\"\n        )\n""",
    "bind cursor authority to team relationship",
)

replace_once(
    "backend/tests/test_round24_hardening.py",
    "with pytest.raises(RuntimeError, match='refusing legacy frontend fallback'):",
    "with pytest.raises(RuntimeError, match='frontend_dist/index.html must exist in production'):",
    "update modern frontend production failure assertion",
)
