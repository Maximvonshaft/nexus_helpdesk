from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ...voice_models import WebchatVoiceSession
from .config import WebCallAISettings, get_webcall_ai_settings


@dataclass(frozen=True)
class WebCallAIPilotGateDecision:
    allowed: bool
    reason: str


def evaluate_webcall_ai_pilot_gate(
    *,
    session: WebchatVoiceSession | None,
    tenant_key: str | None = None,
    settings: WebCallAISettings | None = None,
) -> WebCallAIPilotGateDecision:
    resolved = settings or get_webcall_ai_settings()
    if not resolved.pilot_closure_enabled:
        return WebCallAIPilotGateDecision(False, "pilot_disabled")
    if resolved.pilot_kill_switch:
        return WebCallAIPilotGateDecision(False, "pilot_kill_switch")
    if resolved.app_env == "production":
        return WebCallAIPilotGateDecision(False, "production_rejected")

    if session is not None and _contains(resolved.pilot_session_allowlist, session.public_id):
        return WebCallAIPilotGateDecision(True, "session_allowlist")
    if tenant_key and _contains(resolved.pilot_tenant_allowlist, tenant_key):
        return WebCallAIPilotGateDecision(True, "tenant_allowlist")
    if resolved.pilot_canary_percent > 0:
        key = tenant_key or (session.public_id if session is not None else "pilot")
        if _bucket_fraction(key) < resolved.pilot_canary_percent:
            return WebCallAIPilotGateDecision(True, "canary")
    return WebCallAIPilotGateDecision(False, "not_allowed")


def _contains(csv_value: str | None, candidate: str | None) -> bool:
    if not csv_value or not candidate:
        return False
    return candidate.strip() in {item.strip() for item in csv_value.split(",") if item.strip()}


def _bucket_fraction(key: str) -> float:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return (int(digest[:8], 16) % 10000) / 10000
