from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str, *, label: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}_COUNT={count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "backend/app/services/nexus_osr/release_profiles.py",
    'def safe_reason(value: Any, *, fallback: str = "unknown") -> str:\n    normalized = str(value or "").strip().lower().replace(" ", "_")[:120]\n    return normalized if _REASON_RE.fullmatch(normalized) else fallback\n',
    'def safe_reason(value: Any, *, fallback: str = "unknown") -> str:\n    normalized = str(value or "").strip().lower().replace(" ", "_")[:120]\n    sensitive_tokens = ("bearer", "secret", "token", "password", "authorization", "credential", "cookie", "api_key", "apikey")\n    if any(token in normalized for token in sensitive_tokens):\n        return fallback\n    return normalized if _REASON_RE.fullmatch(normalized) else fallback\n',
    label="SAFE_REASON",
)
replace_once(
    "backend/tests/test_nexus_osr_business_readiness.py",
    'from app import models, models_control_plane, models_operations_dispatch, models_osr, models_webchat_binding  # noqa: F401\n',
    'from app import models, models_control_plane, models_operations_dispatch, models_osr, models_webchat_binding, webchat_models  # noqa: F401\n',
    label="TEST_MODEL_IMPORTS",
)
